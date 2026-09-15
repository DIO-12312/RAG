package httpapi

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestProbeChatReportsProviderFailures(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/chat/completions" {
			t.Errorf("unexpected path %s", r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer secret-key" {
			t.Errorf("missing bearer credential: %q", got)
		}
		var payload map[string]any
		_ = json.NewDecoder(r.Body).Decode(&payload)
		if _, ok := payload["tools"]; ok {
			t.Error("probe must not expose tools")
		}
		_, _ = io.WriteString(w, `{"choices":[{"message":{"role":"assistant","content":"pong"}}]}`)
	}))
	defer server.Close()

	detail, err := probeChat(context.Background(), server.URL+"/v1", "secret-key", "probe-model", time.Second, true)
	if err != nil {
		t.Fatalf("probe failed: %v", err)
	}
	if !strings.Contains(detail, "probe-model") {
		t.Fatalf("unexpected detail: %s", detail)
	}

	unauthorized := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
	}))
	defer unauthorized.Close()
	if _, err := probeChat(context.Background(), unauthorized.URL+"/v1", "k", "probe-model", time.Second, true); err == nil {
		t.Fatal("provider error must surface as a failed probe")
	}
}

func TestProbeEmbeddingValidatesDimension(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/embeddings" {
			t.Errorf("unexpected path %s", r.URL.Path)
		}
		_, _ = io.WriteString(w, `{"data":[{"embedding":[0.1,0.2,0.3]}]}`)
	}))
	defer server.Close()

	detail, err := probeEmbedding(context.Background(), server.URL+"/v1", "k", "embed-model", 3, true)
	if err != nil {
		t.Fatalf("probe failed: %v", err)
	}
	if !strings.Contains(detail, "3 维") {
		t.Fatalf("unexpected detail: %s", detail)
	}
	if _, err := probeEmbedding(context.Background(), server.URL+"/v1", "k", "embed-model", 4, true); err == nil {
		t.Fatal("dimension mismatch must fail the probe")
	}
}

func TestProbeRerankChecksResultShape(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/rerank" {
			t.Errorf("unexpected path %s", r.URL.Path)
		}
		_, _ = io.WriteString(w, `{"results":[{"index":0,"relevance_score":0.9},{"index":1,"relevance_score":0.1}]}`)
	}))
	defer server.Close()

	detail, err := probeRerank(context.Background(), server.URL+"/v1", "k", "rerank-model", true)
	if err != nil {
		t.Fatalf("probe failed: %v", err)
	}
	if !strings.Contains(detail, "2 条") {
		t.Fatalf("unexpected detail: %s", detail)
	}

	short := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = io.WriteString(w, `{"results":[{"index":0,"relevance_score":0.9}]}`)
	}))
	defer short.Close()
	if _, err := probeRerank(context.Background(), short.URL+"/v1", "k", "rerank-model", true); err == nil {
		t.Fatal("incomplete rerank response must fail the probe")
	}
}

func TestProbeEndpointsRespectConfiguredSuffix(t *testing.T) {
	if got := embedEndpoint("https://provider.test/v1/"); got != "https://provider.test/v1/embeddings" {
		t.Fatalf("unexpected embedding endpoint: %s", got)
	}
	if got := rerankEndpoint("https://provider.test/v1"); got != "https://provider.test/v1/rerank" {
		t.Fatalf("unexpected rerank endpoint: %s", got)
	}
	if got := rerankEndpoint("https://provider.test/rerank"); got != "https://provider.test/rerank" {
		t.Fatalf("rerank endpoint must not be duplicated: %s", got)
	}
}

func TestProbeDetailTruncatesLongProviderErrors(t *testing.T) {
	message := probeDetail(errString(strings.Repeat("x", 400)))
	if len([]rune(message)) > probeDetailLimit+1 {
		t.Fatalf("detail must be truncated: %d", len([]rune(message)))
	}
}

type errString string

func (e errString) Error() string { return string(e) }
