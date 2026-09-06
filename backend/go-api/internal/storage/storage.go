package storage

import (
	"context"
	"database/sql"
	"embed"
	"encoding/json"
	"fmt"
	_ "github.com/go-sql-driver/mysql"
	"strings"
	"time"
)

//go:embed schema.sql
var schema embed.FS

type Store struct{ DB *sql.DB }

func Open(dsn string) (*Store, error) {
	db, e := sql.Open("mysql", dsn)
	if e != nil {
		return nil, e
	}
	db.SetMaxOpenConns(12)
	db.SetConnMaxLifetime(3 * time.Minute)
	return &Store{db}, nil
}
func (s *Store) Migrate(ctx context.Context) error {
	conn, e := s.DB.Conn(ctx)
	if e != nil {
		return e
	}
	defer conn.Close()
	var locked int
	if e = conn.QueryRowContext(ctx, "SELECT GET_LOCK('rag-product-migrate',30)").Scan(&locked); e != nil {
		return e
	}
	if locked != 1 {
		return fmt.Errorf("migration lock timed out")
	}
	defer conn.ExecContext(context.Background(), "SELECT RELEASE_LOCK('rag-product-migrate')")
	b, _ := schema.ReadFile("schema.sql")
	for _, q := range strings.Split(string(b), ";") {
		if strings.TrimSpace(q) != "" {
			if _, e = conn.ExecContext(ctx, q); e != nil {
				return e
			}
		}
	}
	_, e = conn.ExecContext(ctx, "INSERT IGNORE INTO schema_migrations(version) VALUES(1)")
	if e != nil {
		return e
	}
	return nil
}

type User struct {
	ID       string `json:"id"`
	Email    string `json:"email"`
	Language string `json:"language"`
	Hash     string `json:"-"`
}

func (s *Store) User(ctx context.Context, email string) (u User, e error) {
	e = s.DB.QueryRowContext(ctx, "SELECT id,email,language,password_hash FROM users WHERE email=?", email).Scan(&u.ID, &u.Email, &u.Language, &u.Hash)
	return
}
func (s *Store) UserID(ctx context.Context, id string) (u User, e error) {
	e = s.DB.QueryRowContext(ctx, "SELECT id,email,language FROM users WHERE id=?", id).Scan(&u.ID, &u.Email, &u.Language)
	return
}
func (s *Store) CreateUser(ctx context.Context, u User) error {
	_, e := s.DB.ExecContext(ctx, "INSERT INTO users(id,email,password_hash,language) VALUES(?,?,?,?)", u.ID, u.Email, u.Hash, u.Language)
	return e
}
func (s *Store) Revoked(ctx context.Context, jti string) (bool, error) {
	var n int
	e := s.DB.QueryRowContext(ctx, "SELECT COUNT(*) FROM jwt_token_revocations WHERE jti=?", jti).Scan(&n)
	return n > 0, e
}
func (s *Store) Revoke(ctx context.Context, jti string, expiry time.Time) error {
	_, e := s.DB.ExecContext(ctx, "INSERT IGNORE INTO jwt_token_revocations(jti,expires_at) VALUES(?,?)", jti, expiry)
	return e
}
func modelTable(kind string) string {
	switch kind {
	case "chat", "embedding", "rerank":
		return kind + "_model_configs"
	}
	panic("invalid model kind")
}
func (s *Store) Model(ctx context.Context, user, kind string) (map[string]any, string, error) {
	var b, secret string
	extra := "'thinkingEnabled',IF(thinking_enabled,CAST('true' AS JSON),CAST('false' AS JSON))"
	if kind == "embedding" {
		extra = "'defaultTopK',default_top_k,'embeddingDimension',embedding_dimension"
	}
	if kind == "rerank" {
		extra = "'topN',top_n"
	}
	e := s.DB.QueryRowContext(ctx, "SELECT JSON_OBJECT('baseUrl',base_url,'modelName',model_name,'timeoutSeconds',timeout_seconds,'apiKeyHint',api_key_hint,'apiKeyConfigured',IF(encrypted_api_key='',CAST('false' AS JSON),CAST('true' AS JSON)),"+extra+"),encrypted_api_key FROM "+modelTable(kind)+" WHERE user_id=?", user).Scan(&b, &secret)
	if e == sql.ErrNoRows {
		return map[string]any{}, "", nil
	}
	m := map[string]any{}
	if e == nil {
		e = json.Unmarshal([]byte(b), &m)
	}
	return m, secret, e
}
func (s *Store) SaveModel(ctx context.Context, user, kind string, m map[string]any, secret string) error {
	cols := []string{"user_id", "base_url", "model_name", "encrypted_api_key", "api_key_hint", "timeout_seconds"}
	args := []any{user, m["baseUrl"], m["modelName"], secret, m["apiKeyHint"], m["timeoutSeconds"]}
	switch kind {
	case "chat":
		cols = append(cols, "thinking_enabled")
		args = append(args, m["thinkingEnabled"])
	case "embedding":
		cols = append(cols, "default_top_k", "embedding_dimension")
		args = append(args, m["defaultTopK"], m["embeddingDimension"])
	case "rerank":
		cols = append(cols, "top_n")
		args = append(args, m["topN"])
	}
	updates := []string{}
	marks := []string{}
	for _, col := range cols {
		marks = append(marks, "?")
		if col != "user_id" {
			updates = append(updates, col+"=VALUES("+col+")")
		}
	}
	_, e := s.DB.ExecContext(ctx, "INSERT INTO "+modelTable(kind)+"("+strings.Join(cols, ",")+") VALUES("+strings.Join(marks, ",")+") ON DUPLICATE KEY UPDATE "+strings.Join(updates, ","), args...)
	return e
}

type Resource struct {
	ID        string `json:"id"`
	UserID    string `json:"-"`
	DatasetID string `json:"datasetId"`
	Kind      string `json:"-"`
	Name      string `json:"name"`
	JobID     string `json:"jobId"`
	UpdatedAt string `json:"updatedAt"`
}

func (s *Store) Put(ctx context.Context, r Resource) error {
	_, e := s.DB.ExecContext(ctx, "INSERT INTO resource_index(id,user_id,dataset_id,kind,name,job_id) VALUES(?,?,?,?,?,?) ON DUPLICATE KEY UPDATE job_id=IF(user_id=VALUES(user_id),VALUES(job_id),job_id)", r.ID, r.UserID, r.DatasetID, r.Kind, r.Name, r.JobID)
	return e
}
func (s *Store) Resource(ctx context.Context, user, id string) (r Resource, e error) {
	var updated time.Time
	e = s.DB.QueryRowContext(ctx, "SELECT id,user_id,dataset_id,kind,name,job_id,updated_at FROM resource_index WHERE user_id=? AND id=?", user, id).Scan(&r.ID, &r.UserID, &r.DatasetID, &r.Kind, &r.Name, &r.JobID, &updated)
	if e == nil {
		r.UpdatedAt = updated.UTC().Format(time.RFC3339)
	}
	return
}
func (s *Store) List(ctx context.Context, user, kind, dataset string) ([]Resource, error) {
	rows, e := s.DB.QueryContext(ctx, "SELECT id,user_id,dataset_id,kind,name,job_id,updated_at FROM resource_index WHERE user_id=? AND kind=? AND (?='' OR dataset_id=?) ORDER BY updated_at DESC", user, kind, dataset, dataset)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []Resource{}
	for rows.Next() {
		r := Resource{}
		var tm time.Time
		if e = rows.Scan(&r.ID, &r.UserID, &r.DatasetID, &r.Kind, &r.Name, &r.JobID, &tm); e != nil {
			return nil, e
		}
		r.UpdatedAt = tm.UTC().Format(time.RFC3339)
		out = append(out, r)
	}
	return out, rows.Err()
}
