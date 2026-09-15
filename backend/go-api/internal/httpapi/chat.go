package httpapi

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"github.com/gin-gonic/gin"
	"log/slog"
	"net/http"
	"rag-mvp/backend/go-api/internal/agent"
	"rag-mvp/backend/go-api/internal/security"
	"time"
)

func (s *Server) chat(c *gin.Context) {
	var p struct {
		DatasetID      string `json:"datasetId"`
		Question       string `json:"question"`
		ConversationID string `json:"conversationId"`
	}
	if c.ShouldBindJSON(&p) != nil || len(p.Question) == 0 || len(p.Question) > 8000 {
		fail(c, 400, "INVALID_INPUT", "问题长度应为 1–8000 字节。")
		return
	}
	if _, ok := s.owned(c, p.DatasetID, "dataset"); !ok {
		return
	}
	if !s.bindEmbedding(c, p.DatasetID) {
		return
	}
	ctx, cancel := context.WithTimeout(c.Request.Context(), 4*time.Minute)
	defer cancel()
	m, secret, e := s.Store.Model(ctx, uid(c), "chat")
	if e != nil || secret == "" {
		fail(c, 409, "MODEL_NOT_CONFIGURED", "请先在设置中配置对话模型和 API Key。")
		return
	}
	apiKey, e := s.Vault.Open(secret, uid(c)+"/chat")
	if e != nil {
		fail(c, 503, "KEY_UNAVAILABLE", "模型密钥解密失败，请重新保存配置。")
		return
	}
	base, _ := m["baseUrl"].(string)
	name, _ := m["modelName"].(string)
	timeout, _ := m["timeoutSeconds"].(float64)
	thinking, _ := m["thinkingEnabled"].(bool)
	if timeout < 1 {
		timeout = 60
	}
	title := []rune(p.Question)
	if len(title) > 60 {
		title = title[:60]
	}
	if p.ConversationID == "" {
		p.ConversationID = security.ID()
	}
	s.mu.Lock()
	busy := s.runs[p.ConversationID]
	if !busy {
		s.runs[p.ConversationID] = true
	}
	s.mu.Unlock()
	if busy {
		fail(c, 409, "CHAT_BUSY", "当前会话正在生成回答。")
		return
	}
	defer func() { s.mu.Lock(); delete(s.runs, p.ConversationID); s.mu.Unlock() }()
	var dataset string
	e = s.Store.DB.QueryRowContext(ctx, "SELECT dataset_id FROM conversations WHERE id=? AND user_id=?", p.ConversationID, uid(c)).Scan(&dataset)
	if e == sql.ErrNoRows {
		_, e = s.Store.DB.ExecContext(ctx, "INSERT INTO conversations(id,user_id,dataset_id,title) VALUES(?,?,?,?)", p.ConversationID, uid(c), p.DatasetID, string(title))
	} else if e != nil {
		fail(c, 503, "LOAD_FAILED", "会话读取失败。")
		return
	} else if dataset != p.DatasetID {
		// 会话只保存最近一次选择，不能把知识库当作不可变外键。这样已删除知识库
		// 的历史会话仍可切换到一个可用知识库继续进行。
		_, e = s.Store.DB.ExecContext(ctx, "UPDATE conversations SET dataset_id=? WHERE id=? AND user_id=?", p.DatasetID, p.ConversationID, uid(c))
	}
	if e != nil {
		fail(c, 503, "SAVE_FAILED", "会话创建失败。")
		return
	}
	// 一个会话可切换知识库，但模型上下文只能使用当前知识库下的消息。
	if _, e = s.Store.DB.ExecContext(ctx, "INSERT INTO conversation_messages(conversation_id,dataset_id,role,content,citations_json) VALUES(?,?,'user',?,'[]')", p.ConversationID, p.DatasetID, p.Question); e != nil {
		fail(c, 503, "SAVE_FAILED", "会话保存失败。")
		return
	}
	rows, e := s.Store.DB.QueryContext(ctx, "SELECT role,content FROM (SELECT id,role,content FROM conversation_messages WHERE conversation_id=? AND dataset_id=? ORDER BY id DESC LIMIT 12) recent ORDER BY id", p.ConversationID, p.DatasetID)
	if e != nil {
		fail(c, 503, "LOAD_FAILED", "会话读取失败。")
		return
	}
	history := []agent.Message{}
	for rows.Next() {
		var msg agent.Message
		if e = rows.Scan(&msg.Role, &msg.Content); e != nil {
			break
		}
		history = append(history, msg)
	}
	if e == nil {
		e = rows.Err()
	}
	rows.Close()
	if e != nil {
		fail(c, 503, "LOAD_FAILED", "会话读取失败。")
		return
	}
	if len(history) > 0 && history[len(history)-1].Role == "user" && history[len(history)-1].Content == p.Question {
		history = history[:len(history)-1]
	}
	emb, _, e := s.Store.Model(ctx, uid(c), "embedding")
	if e != nil {
		fail(c, 503, "CONFIG_UNAVAILABLE", "配置读取失败。")
		return
	}
	top, _ := emb["defaultTopK"].(float64)
	retriever, e := s.retriever(ctx, uid(c), p.DatasetID)
	if e != nil {
		fail(c, 409, "RERANK_NOT_CONFIGURED", "Rerank 配置不可用，请重新保存配置或关闭 Rerank。")
		return
	}
	c.Header("Content-Type", "text/event-stream")
	c.Header("X-Accel-Buffering", "no")
	c.Header("Cache-Control", "no-cache")
	c.Status(200)
	emit := func(event string, data any) error {
		if e := ctx.Err(); e != nil {
			return e
		}
		b, err := json.Marshal(data)
		if err != nil {
			return err
		}
		_ = http.NewResponseController(c.Writer).SetWriteDeadline(time.Now().Add(15 * time.Second))
		_, err = fmt.Fprintf(c.Writer, "event: %s\ndata: %s\n\n", event, b)
		c.Writer.Flush()
		return err
	}
	modelClient := agent.ModelClient(s.AllowLocalModels)
	defer modelClient.CloseIdleConnections()
	// 同一个受限 adapter 同时承担回答与充分性判断，避免第二套凭据或授权路径；
	// Assess 与受限回答共用同一份默认上下文预算；模型调用次数仅记录，不设硬上限。
	model := agent.OpenAI{BaseURL: base, Key: apiKey, Name: name, Timeout: time.Duration(timeout) * time.Second, Thinking: thinking, Client: modelClient}
	budget := agent.DefaultContextBudget()
	h := agent.Harness{
		Model:     model,
		Tool:      retriever,
		TopK:      int(top),
		Streaming: true,
		Assessor:  agent.ModelSufficiencyAssessor{Model: model, Budget: &budget},
		Rewriter:  agent.ModelQueryRewriter{Model: model, Budget: &budget},
		Observer:  agent.JSONLogObserver{Logger: slog.Default()},
		RunID:     agent.NewRunID(),
	}
	answer, citations, e := h.Run(ctx, p.DatasetID, p.Question, history, emit)
	if e != nil {
		code, message := agent.FailureHint(e)
		_ = emit("error", gin.H{"code": code, "message": message})
		return
	}
	b, _ := json.Marshal(citations)
	if _, e = s.Store.DB.ExecContext(ctx, "INSERT INTO conversation_messages(conversation_id,dataset_id,role,content,citations_json) VALUES(?,?,'assistant',?,?)", p.ConversationID, p.DatasetID, answer, b); e != nil {
		_ = emit("error", gin.H{"code": "SAVE_FAILED", "message": "回答生成成功，但会话保存失败。"})
		return
	}
	_ = emit("final", gin.H{"answer": answer, "citations": citations, "conversationId": p.ConversationID})
}
func (s *Server) conversations(c *gin.Context) {
	rows, e := s.Store.DB.QueryContext(c.Request.Context(), "SELECT c.id,c.dataset_id,c.title,c.created_at,COALESCE((SELECT MAX(m.created_at) FROM conversation_messages m WHERE m.conversation_id=c.id),c.created_at) AS last_activity FROM conversations c WHERE c.user_id=? ORDER BY last_activity DESC,c.id DESC LIMIT 100", uid(c))
	if e != nil {
		fail(c, 503, "LOAD_FAILED", "会话加载失败。")
		return
	}
	defer rows.Close()
	out := []gin.H{}
	for rows.Next() {
		var id, dataset, title string
		var createdAt, updatedAt time.Time
		if e = rows.Scan(&id, &dataset, &title, &createdAt, &updatedAt); e != nil {
			fail(c, 503, "LOAD_FAILED", "会话加载失败。")
			return
		}
		out = append(out, gin.H{"id": id, "datasetId": dataset, "title": title, "createdAt": createdAt.UTC().Format(time.RFC3339), "updatedAt": updatedAt.UTC().Format(time.RFC3339)})
	}
	if rows.Err() != nil {
		fail(c, 503, "LOAD_FAILED", "会话加载失败。")
		return
	}
	c.JSON(200, out)
}
func (s *Server) messages(c *gin.Context) {
	var dataset string
	e := s.Store.DB.QueryRowContext(c.Request.Context(), "SELECT dataset_id FROM conversations WHERE id=? AND user_id=?", c.Param("id"), uid(c)).Scan(&dataset)
	if e != nil {
		fail(c, 404, "NOT_FOUND", "会话不存在。")
		return
	}
	if requested := c.Query("datasetId"); requested != "" {
		if _, ok := s.owned(c, requested, "dataset"); !ok {
			return
		}
		dataset = requested
	}
	rows, e := s.Store.DB.QueryContext(c.Request.Context(), "SELECT role,content,citations_json FROM conversation_messages WHERE conversation_id=? AND dataset_id=? ORDER BY id LIMIT 200", c.Param("id"), dataset)
	if e != nil {
		fail(c, 503, "LOAD_FAILED", "消息加载失败。")
		return
	}
	defer rows.Close()
	out := []gin.H{}
	for rows.Next() {
		var role, content string
		var citations json.RawMessage
		if e = rows.Scan(&role, &content, &citations); e != nil {
			fail(c, 503, "LOAD_FAILED", "消息加载失败。")
			return
		}
		out = append(out, gin.H{"role": role, "content": content, "citations": citations})
	}
	if rows.Err() != nil {
		fail(c, 503, "LOAD_FAILED", "消息加载失败。")
		return
	}
	c.JSON(200, out)
}
