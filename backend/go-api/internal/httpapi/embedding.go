package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/gin-gonic/gin"
	"rag-mvp/backend/go-api/internal/ragclient"
	pb "rag-mvp/backend/go-api/internal/ragpb"
	"time"
)

func (s *Server) embeddingSnapshot(ctx context.Context, user string) (string, uint32, string, error) {
	config, secret, e := s.Store.Model(ctx, user, "embedding")
	if e != nil {
		return "", 0, "", e
	}
	if secret == "" {
		return "", 0, "", fmt.Errorf("embedding not configured")
	}
	key, e := s.Vault.Open(secret, user+"/embedding")
	if e != nil {
		return "", 0, "", e
	}
	name, _ := config["modelName"].(string)
	dim, _ := config["embeddingDimension"].(float64)
	config["apiKey"] = key
	b, e := json.Marshal(config)
	if e != nil {
		return "", 0, "", e
	}
	return name, uint32(dim), s.Vault.Seal(string(b), "rag/embedding-profile/v1"), nil
}

func (s *Server) bindEmbedding(c *gin.Context, dataset string) bool {
	ctx, cancel := context.WithTimeout(c.Request.Context(), 15*time.Second)
	defer cancel()
	name, dim, profile, e := s.embeddingSnapshot(ctx, uid(c))
	if e != nil {
		fail(c, 409, "EMBEDDING_NOT_CONFIGURED", "请先在设置中保存 Embedding 模型 URL、模型名称与 API Key。")
		return false
	}
	r, e := s.RAG.RPC.BindEmbeddingProfile(ctx, &pb.BindEmbeddingProfileRequest{Context: ragclient.Context(uid(c) + "-bind-" + dataset), DatasetId: dataset, EmbeddingModel: name, EmbeddingDimension: dim, EncryptedEmbeddingProfile: profile})
	if e != nil || r.GetError() != nil {
		fail(c, 409, "EMBEDDING_BIND_FAILED", "旧知识库需要与原索引相同的 Embedding 模型和维度；请检查配置及 RAG 服务。")
		return false
	}
	return true
}
