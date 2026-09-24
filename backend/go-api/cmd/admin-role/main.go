package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"rag-mvp/backend/go-api/internal/storage"
	"strings"
	"time"
)

func main() {
	userID := flag.String("user-id", "", "existing user ID")
	role := flag.String("role", "", "admin or user")
	flag.Parse()
	if flag.NArg() != 0 || strings.TrimSpace(*userID) == "" || len(*userID) > 64 || !storage.UserRoleValid(*role) {
		fmt.Fprintln(os.Stderr, "usage: product-admin-role --user-id ID --role admin|user")
		os.Exit(2)
	}
	dsn := os.Getenv("PRODUCT_MYSQL_DSN")
	if dsn == "" {
		path := os.Getenv("PRODUCT_MYSQL_DSN_FILE")
		if path == "" {
			path = "/run/secrets/product_mysql_dsn"
		}
		contents, err := os.ReadFile(path)
		if err != nil {
			fmt.Fprintln(os.Stderr, "product database configuration unavailable")
			os.Exit(1)
		}
		dsn = strings.TrimSpace(string(contents))
	}
	store, err := storage.Open(dsn)
	if err != nil {
		fmt.Fprintln(os.Stderr, "product database configuration invalid")
		os.Exit(1)
	}
	defer store.DB.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err = store.Migrate(ctx); err != nil {
		fmt.Fprintln(os.Stderr, "product database migration failed")
		os.Exit(1)
	}
	previous, err := store.SetUserRole(ctx, *userID, *role)
	if err != nil {
		switch {
		case errors.Is(err, storage.ErrUserMissing):
			fmt.Fprintln(os.Stderr, "user not found")
		case errors.Is(err, storage.ErrLastAdmin):
			fmt.Fprintln(os.Stderr, "cannot revoke the last administrator")
		default:
			fmt.Fprintln(os.Stderr, "role update failed")
		}
		os.Exit(1)
	}
	slog.New(slog.NewJSONHandler(os.Stdout, nil)).Info("admin_role_changed",
		"user_id", *userID, "previous_role", previous, "new_role", *role)
}
