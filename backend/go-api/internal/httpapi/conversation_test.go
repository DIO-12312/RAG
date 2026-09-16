package httpapi

import (
	"context"
	"github.com/gin-gonic/gin"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"rag-mvp/backend/go-api/internal/storage"
)

func TestRunKeyIsScopedByUser(t *testing.T) {
	if runKey("user-a", "conv") == runKey("user-b", "conv") {
		t.Fatal("同一会话 id 在不同用户下必须使用不同的并发锁键")
	}
	if runKey("user-a", "conv") != runKey("user-a", "conv") {
		t.Fatal("锁键必须稳定")
	}
}

// TestEnsureConversationIsIdempotentAndRejectsForeignIDs 需要真实 MySQL
// （PRODUCT_TEST_MYSQL_DSN）；它覆盖三类生产问题：并发首条消息的主键冲突、
// 同会话切换知识库、以及用他人会话 id 时的跨租户冲突。
func TestEnsureConversationIsIdempotentAndRejectsForeignIDs(t *testing.T) {
	dsn := os.Getenv("PRODUCT_TEST_MYSQL_DSN")
	if dsn == "" {
		t.Skip("PRODUCT_TEST_MYSQL_DSN required")
	}
	store, err := storage.Open(dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer store.DB.Close()
	ctx := context.Background()
	if err = store.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	s := &Server{Store: store}
	const owner, other = "test-user-owner", "test-user-other"
	const conv = "test-conversation-upsert"
	cleanup := func() {
		_, _ = store.DB.ExecContext(ctx, "DELETE FROM conversations WHERE id=?", conv)
	}
	cleanup()
	defer cleanup()

	if err = s.ensureConversation(ctx, owner, conv, "dataset-a", "t"); err != nil {
		t.Fatalf("首次创建失败: %v", err)
	}
	// 并发首条消息：重复调用必须幂等，不能因为主键冲突返回错误。
	if err = s.ensureConversation(ctx, owner, conv, "dataset-a", "t"); err != nil {
		t.Fatalf("重复创建必须幂等: %v", err)
	}
	// 同一会话切换知识库。
	if err = s.ensureConversation(ctx, owner, conv, "dataset-b", "t"); err != nil {
		t.Fatalf("切换知识库失败: %v", err)
	}
	var dataset string
	if err = store.DB.QueryRowContext(ctx, "SELECT dataset_id FROM conversations WHERE id=?", conv).Scan(&dataset); err != nil {
		t.Fatal(err)
	}
	if dataset != "dataset-b" {
		t.Fatalf("会话应保存最新选择，实际 %q", dataset)
	}
	// 他人使用同一 id：必须返回「已被占用」，由调用方映射为 404，而不是 5xx。
	if err = s.ensureConversation(ctx, other, conv, "dataset-c", "t"); err != errConversationTaken {
		t.Fatalf("他人 id 必须返回 errConversationTaken，实际 %v", err)
	}
	var ownerID string
	if err = store.DB.QueryRowContext(ctx, "SELECT user_id FROM conversations WHERE id=?", conv).Scan(&ownerID); err != nil {
		t.Fatal(err)
	}
	if ownerID != owner {
		t.Fatalf("会话归属被篡改: %q", ownerID)
	}
}

func TestDeleteConversation(t *testing.T) {
	dsn := os.Getenv("PRODUCT_TEST_MYSQL_DSN")
	if dsn == "" {
		t.Skip("PRODUCT_TEST_MYSQL_DSN required")
	}

	store, err := storage.Open(dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer store.DB.Close()

	ctx := context.Background()
	if err = store.Migrate(ctx); err != nil {
		t.Fatal(err)
	}

	s := &Server{Store: store}

	const (
		owner        = "test-delete-owner"
		other        = "test-delete-other"
		conversation = "test-conversation-delete"
	)

	cleanup := func() {
		_, _ = store.DB.ExecContext(ctx,
			"DELETE FROM conversation_messages WHERE conversation_id=?",
			conversation,
		)
		_, _ = store.DB.ExecContext(ctx,
			"DELETE FROM conversations WHERE id=?",
			conversation,
		)
	}
	cleanup()
	defer cleanup()

	if err = s.ensureConversation(ctx, owner, conversation, "dataset-a", "测试会话"); err != nil {
		t.Fatalf("创建测试会话失败: %v", err)
	}

	if _, err = store.DB.ExecContext(
		ctx,
		"INSERT INTO conversation_messages (conversation_id, dataset_id, role, content, citations_json) VALUES (?, ?, ?, ?, ?)",
		conversation,
		"dataset-a",
		"user",
		"测试消息",
		"[]",
	); err != nil {
		t.Fatalf("创建测试消息失败: %v", err)
	}

	req := httptest.NewRequest(http.MethodDelete, "/conversations/"+conversation, nil)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req
	c.Params = gin.Params{{Key: "id", Value: conversation}}
	c.Set("user", owner)

	s.deleteConversation(c)

	if w.Code != http.StatusNoContent {
		t.Fatalf("删除自己的会话应返回 204，实际 %d: %s", w.Code, w.Body.String())
	}

	var count int
	if err = store.DB.QueryRowContext(
		ctx,
		"SELECT COUNT(*) FROM conversations WHERE id=?",
		conversation,
	).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatalf("会话删除后仍存在，count=%d", count)
	}

	if err = store.DB.QueryRowContext(
		ctx,
		"SELECT COUNT(*) FROM conversation_messages WHERE conversation_id=?",
		conversation,
	).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatalf("会话消息删除后仍存在，count=%d", count)
	}

	// 重新创建，验证其他用户不能删除。
	if err = s.ensureConversation(ctx, owner, conversation, "dataset-a", "测试会话"); err != nil {
		t.Fatalf("重新创建测试会话失败: %v", err)
	}

	req = httptest.NewRequest(http.MethodDelete, "/conversations/"+conversation, nil)
	w = httptest.NewRecorder()
	c, _ = gin.CreateTestContext(w)
	c.Request = req
	c.Params = gin.Params{{Key: "id", Value: conversation}}
	c.Set("user", other)

	s.deleteConversation(c)

	if w.Code != http.StatusNotFound {
		t.Fatalf("其他用户删除会话应返回 404，实际 %d: %s", w.Code, w.Body.String())
	}

	if err = store.DB.QueryRowContext(
		ctx,
		"SELECT COUNT(*) FROM conversations WHERE id=?",
		conversation,
	).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 1 {
		t.Fatalf("其他用户请求后会话不应被删除，count=%d", count)
	}

	// 不存在的会话也应返回 404。
	const missing = "test-conversation-does-not-exist"
	req = httptest.NewRequest(http.MethodDelete, "/conversations/"+missing, nil)
	w = httptest.NewRecorder()
	c, _ = gin.CreateTestContext(w)
	c.Request = req
	c.Params = gin.Params{{Key: "id", Value: missing}}
	c.Set("user", owner)

	s.deleteConversation(c)

	if w.Code != http.StatusNotFound {
		t.Fatalf("删除不存在会话应返回 404，实际 %d: %s", w.Code, w.Body.String())
	}
}
