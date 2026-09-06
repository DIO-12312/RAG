// One-time local migration. Never overwrite a saved model or print credentials.
package main

import (
	"bufio"
	"context"
	"encoding/base64"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"rag-mvp/backend/go-api/internal/security"
	"rag-mvp/backend/go-api/internal/storage"
	"strings"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "Embedding import failed; verify source file, database and deployment key (no credentials logged).")
		os.Exit(1)
	}
}
func run() error {
	path := flag.String("env-file", "", "legacy environment file (read-only)")
	flag.Parse()
	f, e := os.Open(*path)
	if e != nil {
		return e
	}
	defer f.Close()
	values := map[string]string{}
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if strings.HasPrefix(line, "#") {
			continue
		}
		k, v, ok := strings.Cut(line, "=")
		if ok {
			values[strings.TrimSpace(k)] = strings.Trim(strings.TrimSpace(v), "\"'")
		}
	}
	if e = scanner.Err(); e != nil {
		return e
	}
	if values["EMBEDDING_MODEL_API_KEY"] == "" || values["EMBEDDING_MODEL_NAME"] == "" || values["EMBEDDING_MODEL_URL"] == "" || values["EMBEDDING_MODEL_DIMENSION"] != "1024" {
		return fmt.Errorf("invalid legacy profile")
	}
	store, e := storage.Open(os.Getenv("PRODUCT_MYSQL_DSN"))
	if e != nil {
		return e
	}
	defer store.DB.Close()
	ctx := context.Background()
	var count int
	var owner string
	if e = store.DB.QueryRow("SELECT COUNT(*) FROM chat_model_configs").Scan(&count); e != nil || count != 1 {
		return fmt.Errorf("requires exactly one Chat owner")
	}
	if e = store.DB.QueryRow("SELECT user_id FROM chat_model_configs LIMIT 1").Scan(&owner); e != nil {
		return e
	}
	old, secret, e := store.Model(ctx, owner, "embedding")
	if e != nil {
		return e
	}
	if secret != "" {
		fmt.Println("Existing Embedding configuration preserved; no changes.")
		return nil
	}
	encoded, e := exec.Command("docker", "exec", "rag-product-api-1", "cat", "/var/lib/product/encryption.key").Output()
	if e != nil {
		return e
	}
	key, e := base64.StdEncoding.DecodeString(strings.TrimSpace(string(encoded)))
	if e != nil {
		return e
	}
	vault, e := security.NewVault(key)
	if e != nil {
		return e
	}
	topK := any(6)
	if v, ok := old["defaultTopK"]; ok {
		topK = v
	}
	apiKey := values["EMBEDDING_MODEL_API_KEY"]
	hint := "****"
	if len(apiKey) > 4 {
		hint += apiKey[len(apiKey)-4:]
	}
	config := map[string]any{"baseUrl": strings.TrimSuffix(strings.TrimRight(values["EMBEDDING_MODEL_URL"], "/"), "/embeddings"), "modelName": values["EMBEDDING_MODEL_NAME"], "timeoutSeconds": 60, "defaultTopK": topK, "embeddingDimension": 1024, "apiKeyHint": hint}
	if e = store.SaveModel(ctx, owner, "embedding", config, vault.Seal(apiKey, owner+"/embedding")); e != nil {
		return e
	}
	fmt.Println("Legacy Embedding configuration imported encrypted; source file unchanged.")
	return nil
}
