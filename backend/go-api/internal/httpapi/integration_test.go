package httpapi

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"net/url"
	"os"
	"os/exec"
	"rag-mvp/backend/go-api/internal/agent"
	"rag-mvp/backend/go-api/internal/ragclient"
	pb "rag-mvp/backend/go-api/internal/ragpb"
	"rag-mvp/backend/go-api/internal/security"
	"rag-mvp/backend/go-api/internal/storage"
	"strings"
	"testing"
	"time"
)

type diagnosticTransport struct {
	base   http.RoundTripper
	report func(string)
}

func (d diagnosticTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	response, err := d.base.RoundTrip(r)
	if err == nil && response.StatusCode >= 400 {
		body, _ := io.ReadAll(io.LimitReader(response.Body, 8192))
		response.Body.Close()
		response.Body = io.NopCloser(bytes.NewReader(body))
		var payload struct {
			Error struct {
				Code    any    `json:"code"`
				Message string `json:"message"`
			} `json:"error"`
		}
		if json.Unmarshal(body, &payload) == nil {
			d.report(fmt.Sprintf("provider error code=%v message=%s", payload.Error.Code, payload.Error.Message))
		}
	}
	return response, err
}

// Uses a real MySQL database and deployed Python RPC/Worker/ES. The chat provider
// is deterministic so no external Chat API key is needed for this integration test.
func TestLiveProductFlow(t *testing.T) {
	dsn := os.Getenv("PRODUCT_TEST_MYSQL_DSN")
	if dsn == "" {
		t.Skip("PRODUCT_TEST_MYSQL_DSN required")
	}
	store, e := storage.Open(dsn)
	if e != nil {
		t.Fatal(e)
	}
	defer store.DB.Close()
	ctx := context.Background()
	if e = store.Migrate(ctx); e != nil {
		t.Fatal(e)
	}
	rag, e := ragclient.New("127.0.0.1:50051")
	if e != nil {
		t.Fatal(e)
	}
	defer rag.Conn.Close()
	encodedKey, e := exec.Command("docker", "exec", "rag-product-api-1", "cat", "/var/lib/product/encryption.key").Output()
	if e != nil {
		t.Fatal("deployment encryption key unavailable")
	}
	sharedKey, e := base64.StdEncoding.DecodeString(strings.TrimSpace(string(encodedKey)))
	if e != nil {
		t.Fatal("deployment encryption key invalid")
	}
	vault, e := security.NewVault(sharedKey)
	if e != nil {
		t.Fatal("deployment encryption key invalid")
	}
	var embeddingOwner string
	var embeddingCount int
	if e = store.DB.QueryRow("SELECT COUNT(*) FROM embedding_model_configs WHERE encrypted_api_key<>''").Scan(&embeddingCount); e != nil || embeddingCount != 1 {
		t.Fatal("acceptance requires exactly one saved Embedding configuration")
	}
	if e = store.DB.QueryRow("SELECT user_id FROM embedding_model_configs WHERE encrypted_api_key<>'' LIMIT 1").Scan(&embeddingOwner); e != nil {
		t.Fatal("saved Embedding configuration unavailable")
	}
	embeddingConfig, embeddingSecret, e := store.Model(ctx, embeddingOwner, "embedding")
	if e != nil {
		t.Fatal("saved Embedding configuration unavailable")
	}
	embeddingKey, e := vault.Open(embeddingSecret, embeddingOwner+"/embedding")
	if e != nil {
		t.Fatal("saved Embedding key unavailable")
	}
	embeddingConfig["apiKey"] = embeddingKey
	modelCalls := 0
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		modelCalls++
		if modelCalls%2 == 1 {
			call := agent.ToolCall{ID: "call-retrieve", Type: "function"}
			call.Function.Name = "rag_retrieve"
			call.Function.Arguments = `{"query":"What is the Project Cobalt launch code?"}`
			_ = json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"message": agent.Message{ToolCalls: []agent.ToolCall{call}}}}})
		} else {
			var body struct {
				Messages []agent.Message `json:"messages"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			last := body.Messages[len(body.Messages)-1]
			if last.Role != "tool" || !strings.Contains(last.Content, "COBALT-742") {
				t.Error("real retrieved evidence missing")
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"message": agent.Message{Content: "The launch code is COBALT-742. [1]"}}}})
		}
	}))
	defer model.Close()
	liveProvider := os.Getenv("PRODUCT_TEST_SAVED_CHAT") == "true"
	chatConfig := map[string]any{"baseUrl": model.URL, "modelName": "deterministic-tool-model", "apiKey": "integration-secret", "timeoutSeconds": 60, "thinkingEnabled": false}
	if liveProvider {
		// Explicit opt-in only: exercise the single saved configuration without
		// logging credentials or changing the original user's settings.
		var count int
		if e := store.DB.QueryRow("SELECT COUNT(*) FROM chat_model_configs").Scan(&count); e != nil || count != 1 {
			t.Fatal("saved Chat acceptance requires exactly one configuration")
		}
		var owner string
		if e := store.DB.QueryRow("SELECT user_id FROM chat_model_configs LIMIT 1").Scan(&owner); e != nil {
			t.Fatal("saved configuration unavailable")
		}
		config, encrypted, e := store.Model(ctx, owner, "chat")
		if e != nil {
			t.Fatal("saved configuration unavailable")
		}
		encoded, e := exec.Command("docker", "exec", "rag-product-api-1", "sh", "-c", `if [ -n "$PRODUCT_ENCRYPTION_KEY" ]; then printf %s "$PRODUCT_ENCRYPTION_KEY"; else cat /var/lib/product/encryption.key; fi`).Output()
		if e != nil {
			t.Fatal("deployment encryption key unavailable")
		}
		key, e := base64.StdEncoding.DecodeString(strings.TrimSpace(string(encoded)))
		if e != nil {
			t.Fatal("deployment encryption key invalid")
		}
		deploymentVault, e := security.NewVault(key)
		if e != nil {
			t.Fatal("deployment encryption key invalid")
		}
		secret, e := deploymentVault.Open(encrypted, owner+"/chat")
		if e != nil {
			t.Fatal("saved API key cannot be decrypted")
		}
		config["apiKey"] = secret
		chatConfig = config
		t.Log("Testing saved Chat provider against a temporary fixture knowledge base")
		modelURL, _ := config["baseUrl"].(string)
		modelName, _ := config["modelName"].(string)
		thinking, _ := config["thinkingEnabled"].(bool)
		timeout, _ := config["timeoutSeconds"].(float64)
		providerClient := agent.ModelClient(false)
		providerClient.Transport = diagnosticTransport{base: providerClient.Transport, report: func(message string) { t.Log(strings.ReplaceAll(message, secret, "[REDACTED]")) }}
		defer providerClient.CloseIdleConnections()
		provider := agent.OpenAI{BaseURL: modelURL, Key: secret, Name: modelName, Thinking: thinking, Timeout: time.Duration(timeout) * time.Second, Client: providerClient}
		if _, e := provider.Complete(ctx, []agent.Message{{Role: "user", Content: "Use rag_retrieve to find the Project Cobalt launch code."}}, true); e != nil {
			t.Fatalf("saved provider tool-call preflight: %v", e)
		}
	}
	s := &Server{Store: store, RAG: rag, Vault: vault, JWTKey: bytes.Repeat([]byte{3}, 32), EmbeddingModel: os.Getenv("PRODUCT_TEST_EMBEDDING_MODEL"), EmbeddingDimension: 1024, AllowLocalModels: !liveProvider}
	server := httptest.NewServer(s.Router())
	defer server.Close()
	jar, _ := cookiejar.New(nil)
	client := &http.Client{Jar: jar, Timeout: 240 * time.Second}
	base, _ := url.Parse(server.URL)
	call := func(method, path string, body io.Reader, contentType string, want int) []byte {
		t.Helper()
		req, _ := http.NewRequest(method, server.URL+path, body)
		req.Header.Set("Content-Type", contentType)
		for _, cookie := range jar.Cookies(base) {
			if cookie.Name == "rag_csrf" {
				req.Header.Set("X-CSRF-Token", cookie.Value)
			}
		}
		resp, e := client.Do(req)
		if e != nil {
			t.Fatal(e)
		}
		defer resp.Body.Close()
		b, _ := io.ReadAll(resp.Body)
		if resp.StatusCode != want {
			t.Fatalf("%s %s got %d: %s", method, path, resp.StatusCode, b)
		}
		return b
	}
	email := "flow-" + security.ID() + "@example.test"
	registered := call("POST", "/auth/register", strings.NewReader(fmt.Sprintf(`{"email":%q,"password":"integration-password"}`, email)), "application/json", 200)
	var user storage.User
	_ = json.Unmarshal(registered, &user)
	defer func() {
		_, _ = store.DB.Exec("DELETE FROM conversation_messages WHERE conversation_id IN (SELECT id FROM conversations WHERE user_id=?)", user.ID)
		for _, table := range []string{"conversations", "resource_index", "chat_model_configs", "embedding_model_configs", "rerank_model_configs", "agent_settings"} {
			_, _ = store.DB.Exec("DELETE FROM "+table+" WHERE user_id=?", user.ID)
		}
		_, _ = store.DB.Exec("DELETE FROM users WHERE id=?", user.ID)
	}()
	// A signed token without CSRF cannot write.
	req, _ := http.NewRequest("POST", server.URL+"/datasets", strings.NewReader(`{"name":"denied"}`))
	req.Header.Set("Content-Type", "application/json")
	resp, e := client.Do(req)
	if e != nil {
		t.Fatal(e)
	}
	resp.Body.Close()
	if resp.StatusCode != 403 {
		t.Fatal("missing CSRF accepted")
	}
	call("GET", "/datasets/not-owned", nil, "", 404)
	configBody, _ := json.Marshal(chatConfig)
	settings := call("PUT", "/settings/models/chat", bytes.NewReader(configBody), "application/json", 200)
	if secret, _ := chatConfig["apiKey"].(string); secret != "" && bytes.Contains(settings, []byte(secret)) {
		t.Fatal("API key leaked")
	}
	call("POST", "/datasets", strings.NewReader(`{"name":"Requires configuration"}`), "application/json", 409)
	embeddingBody, _ := json.Marshal(embeddingConfig)
	embeddingSettings := call("PUT", "/settings/models/embedding", bytes.NewReader(embeddingBody), "application/json", 200)
	if bytes.Contains(embeddingSettings, []byte(embeddingKey)) {
		t.Fatal("Embedding key leaked")
	}
	created := call("POST", "/datasets", strings.NewReader(`{"name":"Integration Cobalt"}`), "application/json", 201)
	var dataset struct {
		ID string `json:"id"`
	}
	_ = json.Unmarshal(created, &dataset)
	defer func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 60*time.Second)
		defer cancel()
		r, e := rag.RPC.DeleteDataset(cleanup, &pb.DeleteDatasetRequest{Context: ragclient.Context(security.ID()), DatasetId: dataset.ID})
		if e != nil || r.GetError() != nil {
			t.Error("RAG cleanup failed")
			return
		}
		for cleanup.Err() == nil {
			j, e := rag.RPC.GetJob(cleanup, &pb.GetJobRequest{JobId: r.GetResult().JobId})
			if e == nil && j.GetError().GetCode() == "JOB_NOT_FOUND" {
				return
			}
			time.Sleep(time.Second)
		}
		t.Error("RAG cleanup timed out")
	}()
	var upload bytes.Buffer
	writer := multipart.NewWriter(&upload)
	part, _ := writer.CreateFormFile("file", "cobalt.txt")
	_, _ = part.Write([]byte("Project Cobalt launch code is COBALT-742. This is the official launch code documented for the integration acceptance test."))
	writer.Close()
	jobData := call("POST", "/datasets/"+dataset.ID+"/documents", &upload, writer.FormDataContentType(), 202)
	var job struct {
		ID string `json:"id"`
	}
	_ = json.Unmarshal(jobData, &job)
	ready := false
	for deadline := time.Now().Add(120 * time.Second); time.Now().Before(deadline); {
		j, e := rag.Job(ctx, job.ID)
		if e == nil && j.Status == pb.JobStatus_JOB_STATUS_SUCCEEDED {
			ready = true
			break
		}
		if e == nil && j.Status == pb.JobStatus_JOB_STATUS_FAILED {
			t.Fatalf("ingestion failed: %s", j.GetFailure().GetCode())
		}
		time.Sleep(time.Second)
	}
	if !ready {
		t.Fatal("ingestion timeout")
	}
	detail := call("GET", "/datasets/"+dataset.ID, nil, "", 200)
	if !bytes.Contains(detail, []byte("INDEXED")) {
		t.Fatal("document not indexed")
	}
	var details struct {
		UpdatedAt string `json:"updatedAt"`
	}
	if json.Unmarshal(detail, &details) != nil {
		t.Fatal("invalid dataset response")
	}
	if _, e := time.Parse(time.RFC3339, details.UpdatedAt); e != nil {
		t.Fatal("dataset timestamp missing")
	}
	stream := call("POST", "/chat/stream", strings.NewReader(fmt.Sprintf(`{"datasetId":%q,"question":"What is the Project Cobalt launch code?"}`, dataset.ID)), "application/json", 200)
	if !bytes.Contains(stream, []byte("event: final")) || !bytes.Contains(stream, []byte("COBALT-742")) || (!liveProvider && modelCalls != 2) || !bytes.Contains(stream, []byte("\"ordinal\":1")) {
		t.Fatalf("chat loop failed: %s", stream)
	}
	list := call("GET", "/conversations", nil, "", 200)
	var conversations []struct {
		ID        string `json:"id"`
		UpdatedAt string `json:"updatedAt"`
	}
	_ = json.Unmarshal(list, &conversations)
	if len(conversations) != 1 {
		t.Fatal("conversation missing")
	}
	if _, e := time.Parse(time.RFC3339, conversations[0].UpdatedAt); e != nil {
		t.Fatal("history activity timestamp missing")
	}
	messages := call("GET", "/conversations/"+conversations[0].ID+"/messages", nil, "", 200)
	if !bytes.Contains(messages, []byte("COBALT-742")) {
		t.Fatal("messages missing")
	}
	// A second real account must not access known IDs owned by the first.
	ownerCookies := jar.Cookies(base)
	otherEmail := "isolation-" + security.ID() + "@example.test"
	otherRegistered := call("POST", "/auth/register", strings.NewReader(fmt.Sprintf(`{"email":%q,"password":"integration-password"}`, otherEmail)), "application/json", 200)
	var other storage.User
	_ = json.Unmarshal(otherRegistered, &other)
	defer store.DB.Exec("DELETE FROM users WHERE id=?", other.ID)
	call("GET", "/datasets/"+dataset.ID, nil, "", 404)
	call("GET", "/datasets/"+dataset.ID+"/jobs", nil, "", 404)
	call("POST", "/jobs/"+job.ID+"/cancel", nil, "", 404)
	call("GET", "/conversations/"+conversations[0].ID+"/messages", nil, "", 404)
	call("POST", "/chat/stream", strings.NewReader(fmt.Sprintf(`{"datasetId":%q,"question":"read someone else's data"}`, dataset.ID)), "application/json", 404)
	if string(call("GET", "/datasets", nil, "", 200)) != "[]" {
		t.Fatal("cross-user dataset list leaked")
	}
	jar.SetCookies(base, ownerCookies)
	call("POST", "/auth/logout", nil, "", 204)
	call("GET", "/me", nil, "", 401)
}
