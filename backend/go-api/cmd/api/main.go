package main

import (
	"context"
	"encoding/base64"
	"log"
	"net/http"
	"os"
	"os/signal"
	"rag-mvp/backend/go-api/internal/httpapi"
	"rag-mvp/backend/go-api/internal/ragclient"
	"rag-mvp/backend/go-api/internal/security"
	"rag-mvp/backend/go-api/internal/storage"
	"time"
)

func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}
func main() {
	encoded, e := loadKey("PRODUCT_ENCRYPTION_KEY", "encryption.key")
	if e != nil {
		log.Fatal(e)
	}
	key, e := base64.StdEncoding.DecodeString(encoded)
	if e != nil || len(key) != 32 {
		log.Fatal("PRODUCT_ENCRYPTION_KEY must be base64 of 32 bytes")
	}
	jwtValue, e := loadKey("PRODUCT_JWT_SECRET", "jwt.key")
	if e != nil {
		log.Fatal(e)
	}
	jwtKey := []byte(jwtValue)
	if len(jwtKey) < 32 {
		log.Fatal("PRODUCT_JWT_SECRET must have at least 32 bytes")
	}
	dsn := os.Getenv("PRODUCT_MYSQL_DSN")
	if dsn == "" {
		log.Fatal("PRODUCT_MYSQL_DSN required; use separate control-plane database with parseTime=true")
	}
	store, e := storage.Open(dsn)
	if e != nil {
		log.Fatal("invalid MySQL config")
	}
	defer store.DB.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	if e = store.Migrate(ctx); e != nil {
		log.Fatal("MySQL migration failed: ", e)
	}
	vault, e := security.NewVault(key)
	if e != nil {
		log.Fatal(e)
	}
	rag, e := ragclient.New(env("PRODUCT_RAG_TARGET", "127.0.0.1:50051"))
	if e != nil {
		log.Fatal(e)
	}
	defer rag.Conn.Close()
	app := &httpapi.Server{Store: store, RAG: rag, Vault: vault, JWTKey: jwtKey, Origin: env("PRODUCT_ORIGIN", "http://127.0.0.1:5173"), Secure: env("PRODUCT_COOKIE_SECURE", "false") == "true", AllowLocalModels: os.Getenv("PRODUCT_ALLOW_LOCAL_MODELS") == "true"}
	srv := &http.Server{Addr: env("PRODUCT_HTTP_ADDR", "127.0.0.1:8080"), Handler: app.Router(), ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 1 << 20}
	stop, done := signal.NotifyContext(context.Background(), os.Interrupt)
	defer done()
	go func() {
		<-stop.Done()
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		_ = srv.Shutdown(ctx)
	}()
	log.Printf("Product API listening on %s", srv.Addr)
	if e = srv.ListenAndServe(); e != nil && e != http.ErrServerClosed {
		log.Fatal(e)
	}
}
