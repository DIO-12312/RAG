package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/gin-gonic/gin"
	"rag-mvp/backend/go-api/internal/agent"
)

func (s *Server) rerankConfig(ctx context.Context, user string) (map[string]any, string, error) {
	m, secret, err := s.Store.Model(ctx, user, "rerank")
	if err != nil {
		return nil, "", err
	}
	base, _ := m["baseUrl"].(string)
	name, _ := m["modelName"].(string)
	timeout, _ := m["timeoutSeconds"].(float64)
	top, _ := m["topN"].(float64)
	if secret == "" || name == "" || !validURL(base, false) || timeout < 1 || timeout > 300 || top < 1 || top > 20 {
		return nil, "", fmt.Errorf("incomplete rerank configuration")
	}
	key, err := s.Vault.Open(secret, user+"/rerank")
	if err != nil || key == "" {
		return nil, "", fmt.Errorf("rerank key unavailable")
	}
	return m, key, nil
}

func (s *Server) saveAgentSettings(c *gin.Context) {
	var p struct {
		Enabled *bool `json:"rerankEnabled"`
	}
	if c.ShouldBindJSON(&p) != nil || p.Enabled == nil {
		fail(c, 400, "INVALID_INPUT", "请指定是否启用 Rerank。")
		return
	}
	if *p.Enabled {
		if _, _, err := s.rerankConfig(c.Request.Context(), uid(c)); err != nil {
			fail(c, 409, "RERANK_NOT_CONFIGURED", "请先保存有效的 Rerank HTTPS 地址、模型和 API Key。")
			return
		}
	}
	if err := s.Store.SetRerankEnabled(c.Request.Context(), uid(c), *p.Enabled); err != nil {
		fail(c, 503, "SAVE_FAILED", "设置保存失败，请重试。")
		return
	}
	s.settings(c)
}

func (s *Server) retriever(ctx context.Context, user, dataset string) (agent.Retriever, error) {
	enabled, err := s.Store.RerankEnabled(ctx, user)
	if err != nil {
		return nil, err
	}
	if !enabled {
		return s.RAG, nil
	}
	m, key, err := s.rerankConfig(ctx, user)
	if err != nil {
		return nil, err
	}
	profile, err := json.Marshal(map[string]any{"baseUrl": m["baseUrl"], "modelName": m["modelName"], "apiKey": key, "timeoutSeconds": m["timeoutSeconds"]})
	if err != nil {
		return nil, err
	}
	encrypted := s.Vault.Seal(string(profile), "rag/rerank-profile/v1/"+dataset)
	return s.RAG.WithRerank(encrypted, int(m["topN"].(float64)), int(m["timeoutSeconds"].(float64))), nil
}
