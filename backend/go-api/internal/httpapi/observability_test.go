package httpapi

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"rag-mvp/backend/go-api/internal/security"
	"rag-mvp/backend/go-api/internal/storage"
)

func TestObservabilityAuthorizationReadsCurrentRole(t *testing.T) {
	dsn := os.Getenv("PRODUCT_ROLE_TEST_MYSQL_DSN")
	if dsn == "" {
		t.Skip("PRODUCT_ROLE_TEST_MYSQL_DSN requires isolated MySQL")
	}
	store, err := storage.Open(dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer store.DB.Close()
	ctx := context.Background()
	if err := store.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	userID := "observability-admin-test"
	_, _ = store.DB.ExecContext(ctx, "DELETE FROM users WHERE id=?", userID)
	if err := store.CreateUser(ctx, storage.User{ID: userID, Email: "observability-admin-test@example.test", Language: "zh-CN", Hash: "test"}); err != nil {
		t.Fatal(err)
	}
	defer store.DB.ExecContext(ctx, "DELETE FROM users WHERE id=?", userID)
	key := bytes.Repeat([]byte{7}, 32)
	token, _, err := security.Sign(key, userID)
	if err != nil {
		t.Fatal(err)
	}
	server := (&Server{Store: store, JWTKey: key}).Router()
	request := func() int {
		req := httptest.NewRequest(http.MethodGet, "/admin/observability/metrics?window=1h", nil)
		req.AddCookie(&http.Cookie{Name: "rag_token", Value: token})
		response := httptest.NewRecorder()
		server.ServeHTTP(response, req)
		return response.Code
	}
	if got := request(); got != http.StatusForbidden {
		t.Fatalf("ordinary user: %d", got)
	}
	if _, err := store.SetUserRole(ctx, userID, "admin"); err != nil {
		t.Fatal(err)
	}
	if got := request(); got != http.StatusServiceUnavailable {
		t.Fatalf("admin without backend: %d", got)
	}
	// Add a second administrator so revoking the test user preserves the last-admin invariant.
	otherID := "observability-admin-backup"
	_, _ = store.DB.ExecContext(ctx, "DELETE FROM users WHERE id=?", otherID)
	if err := store.CreateUser(ctx, storage.User{ID: otherID, Email: "observability-admin-backup@example.test", Language: "zh-CN", Hash: "test"}); err != nil {
		t.Fatal(err)
	}
	defer store.DB.ExecContext(ctx, "DELETE FROM users WHERE id=?", otherID)
	if _, err := store.SetUserRole(ctx, otherID, "admin"); err != nil {
		t.Fatal(err)
	}
	if _, err := store.SetUserRole(ctx, userID, "user"); err != nil {
		t.Fatal(err)
	}
	if got := request(); got != http.StatusForbidden {
		t.Fatalf("revoked session: %d", got)
	}
}
