package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/gin-gonic/gin"
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
	var dataset string
	e = s.Store.DB.QueryRowContext(ctx, "SELECT dataset_id FROM conversations WHERE id=? AND user_id=?", p.ConversationID, uid(c)).Scan(&dataset)
	if e != nil {
		_, e = s.Store.DB.ExecContext(ctx, "INSERT INTO conversations(id,user_id,dataset_id,title) VALUES(?,?,?,?)", p.ConversationID, uid(c), p.DatasetID, string(title))
	} else if dataset != p.DatasetID {
		fail(c, 404, "NOT_FOUND", "会话不存在或知识库不匹配。")
		return
	}
	if e != nil {
		fail(c, 503, "SAVE_FAILED", "会话创建失败。")
		return
	}
	// 立即持久化用户提问：即使回答流尚未完成，历史记录也能完整恢复该会话。
	if _, e = s.Store.DB.ExecContext(ctx, "INSERT INTO conversation_messages(conversation_id,role,content,citations_json) VALUES(?,'user',?,'[]')", p.ConversationID, p.Question); e != nil {
		fail(c, 503, "SAVE_FAILED", "会话保存失败。")
		return
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
	rows, e := s.Store.DB.QueryContext(ctx, "SELECT role,content FROM (SELECT id,role,content FROM conversation_messages WHERE conversation_id=? ORDER BY id DESC LIMIT 12) recent ORDER BY id", p.ConversationID)
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
	h := agent.Harness{Model: agent.OpenAI{BaseURL: base, Key: apiKey, Name: name, Timeout: time.Duration(timeout) * time.Second, Thinking: thinking, Client: modelClient}, Tool: retriever, MaxRounds: 6, TopK: int(top)}
	answer, citations, e := h.Run(ctx, p.DatasetID, p.Question, history, emit)
	if e != nil {
		_ = emit("error", gin.H{"code": "CHAT_FAILED", "message": "问答未完成，请检查模型连通性、工具调用支持及知识库状态。"})
		return
	}
	b, _ := json.Marshal(citations)
	if _, e = s.Store.DB.ExecContext(ctx, "INSERT INTO conversation_messages(conversation_id,role,content,citations_json) VALUES(?,'assistant',?,?)", p.ConversationID, answer, b); e != nil {
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
	var exists int
	e := s.Store.DB.QueryRowContext(c.Request.Context(), "SELECT COUNT(*) FROM conversations WHERE id=? AND user_id=?", c.Param("id"), uid(c)).Scan(&exists)
	if e != nil || exists != 1 {
		fail(c, 404, "NOT_FOUND", "会话不存在。")
		return
	}
	rows, e := s.Store.DB.QueryContext(c.Request.Context(), "SELECT role,content,citations_json FROM conversation_messages WHERE conversation_id=? ORDER BY id LIMIT 200", c.Param("id"))
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
