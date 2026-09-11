package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/gin-gonic/gin"
	"rag-mvp/backend/go-api/internal/security"
	"rag-mvp/backend/go-api/internal/storage"
)

// Uses only a separately provisioned test database, never the running product database.
func TestRerankSettingsPersistence(t *testing.T) {
	dsn := os.Getenv("RERANK_TEST_MYSQL_DSN")
	if dsn == "" {
		t.Skip("RERANK_TEST_MYSQL_DSN not set")
	}
	store, err := storage.Open(dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer store.DB.Close()
	if err = store.Migrate(context.Background()); err != nil {
		t.Fatal(err)
	}
	user := "rerank-test-" + security.ID()
	defer store.DB.Exec("DELETE FROM agent_settings WHERE user_id=?", user)
	defer store.DB.Exec("DELETE FROM rerank_model_configs WHERE user_id=?", user)
	vault, _ := security.NewVault(bytes.Repeat([]byte{3}, 32))
	s := &Server{Store: store, Vault: vault}
	r := gin.New()
	r.Use(func(c *gin.Context) { c.Set("user", user) })
	r.GET("/settings", s.settings)
	r.PUT("/settings/models/:kind", s.saveModel)
	r.PUT("/settings/agent", s.saveAgentSettings)
	call := func(path, body string, want int) map[string]any {
		t.Helper()
		method := "PUT"
		if body == "" {
			method = "GET"
		}
		req := httptest.NewRequest(method, path, bytes.NewBufferString(body))
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		r.ServeHTTP(w, req)
		if w.Code != want {
			t.Fatalf("%s: got %d: %s", path, w.Code, w.Body.String())
		}
		var result map[string]any
		if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		if bytes.Contains(w.Body.Bytes(), []byte("secret-test-key")) {
			t.Fatal("key leaked")
		}
		return result
	}
	if call("/settings", "", 200)["rerankEnabled"] != false {
		t.Fatal("enabled by default")
	}
	call("/settings/agent", `{"rerankEnabled":true}`, 409)
	call("/settings/models/rerank", `{"baseUrl":"https://provider.test/v1","modelName":"reranker","timeoutSeconds":30,"topN":3}`, 400)
	call("/settings/models/rerank", `{"baseUrl":"https://provider.test/v1","modelName":"reranker","timeoutSeconds":30,"topN":3,"apiKey":"secret-test-key"}`, 200)
	call("/settings/agent", `{"rerankEnabled":true}`, 200)
	if call("/settings", "", 200)["rerankEnabled"] != true {
		t.Fatal("enable not persisted")
	}
	if enabled, err := store.RerankEnabled(context.Background(), "other-user"); err != nil || enabled {
		t.Fatal("user isolation failed")
	}
	m, key, err := s.rerankConfig(context.Background(), user)
	if err != nil || key != "secret-test-key" || m["topN"] != float64(3) {
		t.Fatal("configuration unavailable")
	}
	call("/settings/agent", `{"rerankEnabled":false}`, 200)
	if call("/settings", "", 200)["rerankEnabled"] != false {
		t.Fatal("disable not persisted")
	}
	if _, key, err := s.rerankConfig(context.Background(), user); err != nil || key != "secret-test-key" {
		t.Fatal("disable erased configuration")
	}
	call("/settings/agent", `{}`, 400)
}
