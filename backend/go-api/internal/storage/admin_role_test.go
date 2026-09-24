package storage

import (
	"context"
	"errors"
	"os"
	"sync"
	"testing"
)

func TestUserRoleValidation(t *testing.T) {
	for _, role := range []string{"user", "admin"} {
		if !UserRoleValid(role) {
			t.Fatal(role)
		}
	}
	for _, role := range []string{"", "owner", "ADMIN"} {
		if UserRoleValid(role) {
			t.Fatal(role)
		}
	}
}

// This test requires a disposable, isolated MySQL database. Never point it at
// a running product database because it creates and removes test users.
func TestAdminDemotionKeepsAtLeastOneAdministrator(t *testing.T) {
	dsn := os.Getenv("PRODUCT_ROLE_TEST_MYSQL_DSN")
	if dsn == "" {
		t.Skip("PRODUCT_ROLE_TEST_MYSQL_DSN requires isolated MySQL")
	}
	store, err := Open(dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer store.DB.Close()
	ctx := context.Background()
	if err := store.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"role-test-a", "role-test-b"} {
		if _, err := store.DB.ExecContext(ctx, "DELETE FROM users WHERE id=?", id); err != nil {
			t.Fatal(err)
		}
		if err := store.CreateUser(ctx, User{ID: id, Email: id + "@example.test", Language: "zh-CN", Role: "admin", Hash: "test"}); err != nil {
			t.Fatal(err)
		}
		defer store.DB.ExecContext(ctx, "DELETE FROM users WHERE id=?", id)
		role, err := store.UserRole(ctx, id)
		if err != nil || role != "user" {
			t.Fatalf("registration elevated role: %q %v", role, err)
		}
		if _, err := store.SetUserRole(ctx, id, "admin"); err != nil {
			t.Fatal(err)
		}
	}
	var wg sync.WaitGroup
	results := make(chan error, 2)
	for _, id := range []string{"role-test-a", "role-test-b"} {
		wg.Add(1)
		go func(id string) { defer wg.Done(); _, err := store.SetUserRole(ctx, id, "user"); results <- err }(id)
	}
	wg.Wait()
	close(results)
	succeeded, rejected := 0, 0
	for err := range results {
		if err == nil {
			succeeded++
		} else if errors.Is(err, ErrLastAdmin) {
			rejected++
		} else {
			t.Fatal(err)
		}
	}
	if succeeded != 1 || rejected != 1 {
		t.Fatalf("demotions succeeded=%d rejected=%d", succeeded, rejected)
	}
	var admins int
	if err := store.DB.QueryRowContext(ctx, "SELECT COUNT(*) FROM users WHERE role='admin'").Scan(&admins); err != nil || admins != 1 {
		t.Fatalf("admins=%d error=%v", admins, err)
	}
	if _, err := store.SetUserRole(ctx, "missing-user", "admin"); !errors.Is(err, ErrUserMissing) {
		t.Fatal(err)
	}
	if _, err := store.SetUserRole(ctx, "role-test-a", "owner"); !errors.Is(err, ErrInvalidRole) {
		t.Fatal(err)
	}
}
