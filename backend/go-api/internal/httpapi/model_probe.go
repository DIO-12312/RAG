package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"rag-mvp/backend/go-api/internal/agent"
)

// 探测超时上限：即使配置里的超时更长，连接性测试也必须快速返回。
const probeTimeoutCeiling = 30 * time.Second

// probeDetailLimit 限制回显给前端的错误长度，避免把供应商原始响应整体暴露。
const probeDetailLimit = 200

// testModel 用已保存的配置做一次最小调用，验证模型是否真正可用。
func (s *Server) testModel(c *gin.Context) {
	kind := c.Param("kind")
	if kind != "chat" && kind != "embedding" && kind != "rerank" {
		fail(c, 404, "NOT_FOUND", "模型类型不存在。")
		return
	}
	ctx, cancel := context.WithTimeout(c.Request.Context(), probeTimeoutCeiling)
	defer cancel()
	m, secret, e := s.Store.Model(ctx, uid(c), kind)
	if e != nil {
		fail(c, 503, "CONFIG_UNAVAILABLE", "配置暂不可用。")
		return
	}
	base, _ := m["baseUrl"].(string)
	name, _ := m["modelName"].(string)
	if strings.TrimSpace(base) == "" || strings.TrimSpace(name) == "" {
		fail(c, 409, "MODEL_NOT_CONFIGURED", "请先保存该模型的 Base URL 与模型名称。")
		return
	}
	if secret == "" {
		fail(c, 409, "MODEL_NOT_CONFIGURED", "请先填写并保存 API Key。")
		return
	}
	if !validURL(base, s.AllowLocalModels && kind != "rerank") {
		fail(c, 400, "INVALID_INPUT", "模型 Base URL 无效。")
		return
	}
	key, e := s.Vault.Open(secret, uid(c)+"/"+kind)
	if e != nil || key == "" {
		fail(c, 503, "KEY_UNAVAILABLE", "模型密钥解密失败，请重新保存配置。")
		return
	}
	timeout := probeTimeoutCeiling
	if configured, ok := m["timeoutSeconds"].(float64); ok && configured >= 1 && configured <= 300 {
		timeout = time.Duration(configured) * time.Second
		if timeout > probeTimeoutCeiling {
			timeout = probeTimeoutCeiling
		}
	}
	allowLocal := s.AllowLocalModels && kind != "rerank"
	started := time.Now()
	var detail string
	switch kind {
	case "chat":
		detail, e = probeChat(ctx, base, key, name, timeout, allowLocal)
	case "embedding":
		dimension := 0
		if value, ok := m["embeddingDimension"].(float64); ok {
			dimension = int(value)
		}
		detail, e = probeEmbedding(ctx, base, key, name, dimension, allowLocal)
	case "rerank":
		detail, e = probeRerank(ctx, base, key, name, allowLocal)
	}
	latency := time.Since(started).Milliseconds()
	if e != nil {
		c.JSON(200, gin.H{"ok": false, "latencyMs": latency, "detail": probeDetail(e)})
		return
	}
	c.JSON(200, gin.H{"ok": true, "latencyMs": latency, "detail": detail})
}

// probeDetail 把探测错误压缩为可展示的一句话，不泄露密钥与完整响应体。
func probeDetail(err error) string {
	message := strings.Join(strings.Fields(err.Error()), " ")
	runes := []rune(message)
	if len(runes) > probeDetailLimit {
		message = string(runes[:probeDetailLimit]) + "…"
	}
	if message == "" {
		message = "供应商未返回可用结果"
	}
	return message
}

// probeChat 用一次无工具的最小补全验证对话模型可用。
func probeChat(ctx context.Context, base, key, name string, timeout time.Duration, allowLocal bool) (string, error) {
	if timeout <= 0 {
		timeout = probeTimeoutCeiling
	}
	client := agent.ModelClient(allowLocal)
	defer client.CloseIdleConnections()
	model := agent.OpenAI{BaseURL: base, Key: key, Name: name, Timeout: timeout, Client: client}
	msg, err := model.Complete(ctx, []agent.Message{{Role: "user", Content: "ping"}}, agent.ToolPolicy{Mode: agent.ToolNone})
	if err != nil {
		return "", err
	}
	if strings.TrimSpace(msg.Content) == "" && len(msg.ToolCalls) == 0 {
		return "", fmt.Errorf("模型返回了空响应")
	}
	return fmt.Sprintf("对话模型 %s 响应正常", name), nil
}

// probeEmbedding 调用 /embeddings 并校验返回维度与索引维度一致。
func probeEmbedding(ctx context.Context, base, key, name string, dimension int, allowLocal bool) (string, error) {
	payload, err := json.Marshal(map[string]any{"model": name, "input": "ping"})
	if err != nil {
		return "", err
	}
	var response struct {
		Data []struct {
			Embedding []float64 `json:"embedding"`
		} `json:"data"`
	}
	if err := postProbe(ctx, embedEndpoint(base), key, payload, allowLocal, &response); err != nil {
		return "", err
	}
	if len(response.Data) == 0 || len(response.Data[0].Embedding) == 0 {
		return "", fmt.Errorf("Embedding 接口未返回向量")
	}
	got := len(response.Data[0].Embedding)
	if dimension > 0 && got != dimension {
		return "", fmt.Errorf("返回向量为 %d 维，与索引要求的 %d 维不一致", got, dimension)
	}
	return fmt.Sprintf("Embedding 模型返回 %d 维向量", got), nil
}

// probeRerank 调用 /rerank 并校验返回条数与输入一致，协议与 Python 侧保持一致。
func probeRerank(ctx context.Context, base, key, name string, allowLocal bool) (string, error) {
	documents := []string{"The migration window ends in December.", "Unrelated sentence."}
	payload, err := json.Marshal(map[string]any{
		"model":            name,
		"query":            "When does the migration window end?",
		"documents":        documents,
		"top_n":            len(documents),
		"return_documents": false,
	})
	if err != nil {
		return "", err
	}
	var response struct {
		Results []struct {
			Index          *int    `json:"index"`
			RelevanceScore float64 `json:"relevance_score"`
		} `json:"results"`
	}
	if err := postProbe(ctx, rerankEndpoint(base), key, payload, allowLocal, &response); err != nil {
		return "", err
	}
	if len(response.Results) != len(documents) {
		return "", fmt.Errorf("Rerank 接口返回 %d 条结果，期望 %d 条", len(response.Results), len(documents))
	}
	seen := map[int]bool{}
	for _, result := range response.Results {
		if result.Index == nil || *result.Index < 0 || *result.Index >= len(documents) || seen[*result.Index] {
			return "", fmt.Errorf("Rerank 接口返回了非法的结果下标")
		}
		seen[*result.Index] = true
	}
	return fmt.Sprintf("Rerank 接口返回 %d 条排序结果", len(response.Results)), nil
}

func embedEndpoint(base string) string {
	return strings.TrimRight(base, "/") + "/embeddings"
}

func rerankEndpoint(base string) string {
	trimmed := strings.TrimRight(base, "/")
	if strings.HasSuffix(trimmed, "/rerank") {
		return trimmed
	}
	return trimmed + "/rerank"
}

// postProbe 发送一次带 Bearer 凭据的 JSON 请求，并把响应解析到目标结构。
func postProbe(ctx context.Context, endpoint, key string, payload []byte, allowLocal bool, target any) error {
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(payload))
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer "+key)
	client := agent.ModelClient(allowLocal)
	defer client.CloseIdleConnections()
	response, err := client.Do(request)
	if err != nil {
		return fmt.Errorf("无法连接供应商：%v", err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if err != nil {
		return fmt.Errorf("读取供应商响应失败")
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return fmt.Errorf("供应商返回 %s", response.Status)
	}
	if err := json.Unmarshal(body, target); err != nil {
		return fmt.Errorf("供应商响应不是预期的 JSON 结构")
	}
	return nil
}
