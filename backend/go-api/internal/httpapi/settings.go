package httpapi

import (
	"github.com/gin-gonic/gin"
	"net"
	"net/url"
	"strings"
)

func defaults(kind string) map[string]any {
	m := map[string]any{"kind": kind, "baseUrl": "", "modelName": "", "timeoutSeconds": 60, "apiKeyConfigured": false, "apiKeyHint": nil}
	switch kind {
	case "chat":
		m["thinkingEnabled"] = false
	case "embedding":
		m["embeddingDimension"] = 1024
		m["defaultTopK"] = 6
	case "rerank":
		m["topN"] = 3
	}
	return m
}
func (s *Server) settings(c *gin.Context) {
	out := gin.H{"rerankEnabled": false, "embeddingManagedByServer": false, "rerankAvailable": false}
	for _, kind := range []string{"chat", "embedding", "rerank"} {
		m, _, e := s.Store.Model(c.Request.Context(), uid(c), kind)
		if e != nil {
			fail(c, 503, "CONFIG_UNAVAILABLE", "配置暂不可用。")
			return
		}
		d := defaults(kind)
		for k, v := range m {
			d[k] = v
		}
		out[kind] = d
	}
	c.JSON(200, out)
}
func validURL(value string, local bool) bool {
	u, e := url.Parse(value)
	if e != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return false
	}
	if u.Scheme != "https" && !(local && u.Scheme == "http") {
		return false
	}
	ip := net.ParseIP(u.Hostname())
	if !local && (u.Hostname() == "localhost" || strings.HasSuffix(u.Hostname(), ".local") || (ip != nil && (ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsUnspecified()))) {
		return false
	}
	return true
}
func (s *Server) saveModel(c *gin.Context) {
	kind := c.Param("kind")
	if kind != "chat" && kind != "embedding" && kind != "rerank" {
		fail(c, 404, "NOT_FOUND", "模型类型不存在。")
		return
	}
	var p struct {
		BaseURL   string `json:"baseUrl"`
		Name      string `json:"modelName"`
		Key       string `json:"apiKey"`
		Timeout   int    `json:"timeoutSeconds"`
		Thinking  bool   `json:"thinkingEnabled"`
		TopK      int    `json:"defaultTopK"`
		Dimension uint32 `json:"embeddingDimension"`
		TopN      int    `json:"topN"`
	}
	if c.ShouldBindJSON(&p) != nil || p.Timeout < 1 || p.Timeout > 300 || len(p.Key) > 4096 || len(p.Name) > 200 {
		fail(c, 400, "INVALID_INPUT", "模型参数无效，超时应为 1–300 秒。")
		return
	}
	if kind == "embedding" {
		if p.Dimension != 1024 || p.TopK < 1 || p.TopK > 30 {
			fail(c, 400, "INVALID_EMBEDDING_CONFIG", "当前索引要求 1024 维，Top-K 为 1–30。")
			return
		}
	}
	if !validURL(p.BaseURL, s.AllowLocalModels) || strings.TrimSpace(p.Name) == "" {
		fail(c, 400, "INVALID_INPUT", "请输入有效模型名称和 API Base URL。")
		return
	}
	old, encrypted, e := s.Store.Model(c.Request.Context(), uid(c), kind)
	if e != nil {
		fail(c, 503, "CONFIG_UNAVAILABLE", "配置暂不可用。")
		return
	}
	m := defaults(kind)
	m["baseUrl"] = p.BaseURL
	m["modelName"] = p.Name
	m["timeoutSeconds"] = p.Timeout
	m["apiKeyHint"] = old["apiKeyHint"]
	if p.Key != "" {
		encrypted = s.Vault.Seal(p.Key, uid(c)+"/"+kind)
		hint := "****"
		if len(p.Key) > 4 {
			hint += p.Key[len(p.Key)-4:]
		}
		m["apiKeyHint"] = hint
	}
	m["apiKeyConfigured"] = encrypted != ""
	if kind == "embedding" && encrypted == "" {
		fail(c, 400, "API_KEY_REQUIRED", "首次配置需要填写 API Key。")
		return
	}
	switch kind {
	case "chat":
		m["thinkingEnabled"] = p.Thinking
	case "embedding":
		m["defaultTopK"] = p.TopK
		m["embeddingDimension"] = p.Dimension
	case "rerank":
		if p.TopN < 1 || p.TopN > 30 {
			fail(c, 400, "INVALID_INPUT", "Top-N 应为 1–30。")
			return
		}
		m["topN"] = p.TopN
	}
	if e = s.Store.SaveModel(c.Request.Context(), uid(c), kind, m, encrypted); e != nil {
		fail(c, 503, "SAVE_FAILED", "保存失败，请重试。")
		return
	}
	s.settings(c)
}
