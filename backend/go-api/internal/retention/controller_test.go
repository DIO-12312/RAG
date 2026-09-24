package retention

import (
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
)

func TestCapacityShortensRetentionAndPausesOnlyTraces(t *testing.T) {
	data := t.TempDir()
	overrides := t.TempDir()
	backendCalls := atomic.Int32{}
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		backendCalls.Add(1)
		if r.URL.Path != "/v1/traces" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		if r.Header.Get("Content-Encoding") != "gzip" {
			t.Errorf("OTLP content encoding was lost: %q", r.Header.Get("Content-Encoding"))
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer backend.Close()
	controller, err := New(Config{DataPath: data, OverrideDir: overrides, BudgetBytes: 1 << 20, TempoURL: backend.URL})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(data, "block"), make([]byte, 850000), 0600); err != nil {
		t.Fatal(err)
	}
	s := controller.Check()
	if s.RetentionHours != 96 || s.Paused || s.Error != "" {
		t.Fatalf("unexpected high-water state: %+v", s)
	}
	content, err := os.ReadFile(filepath.Join(overrides, "overrides.yaml"))
	if err != nil || !strings.Contains(string(content), "block_retention: 96h") || !strings.Contains(string(content), "rate_limit_bytes: 15000000") {
		t.Fatalf("override was not written: %s, %v", content, err)
	}
	request := httptest.NewRequest(http.MethodPost, "/v1/traces", strings.NewReader("trace"))
	request.Header.Set("Content-Encoding", "gzip")
	response := httptest.NewRecorder()
	controller.ServeHTTP(response, request)
	if response.Code != http.StatusOK || backendCalls.Load() != 1 {
		t.Fatalf("normal trace was not forwarded: %d, calls=%d", response.Code, backendCalls.Load())
	}
	if err := os.WriteFile(filepath.Join(data, "block"), make([]byte, 950000), 0600); err != nil {
		t.Fatal(err)
	}
	s = controller.Check()
	if !s.Paused || s.RetentionHours != 48 {
		t.Fatalf("unexpected emergency state: %+v", s)
	}
	restarted, err := New(Config{DataPath: data, OverrideDir: overrides, BudgetBytes: 1 << 20, TempoURL: backend.URL})
	if err != nil {
		t.Fatal(err)
	}
	if got := restarted.Check().RetentionHours; got != 24 {
		t.Fatalf("restart forgot the previous retention: %dh", got)
	}
	response = httptest.NewRecorder()
	controller.ServeHTTP(response, request)
	if response.Code != http.StatusTooManyRequests || backendCalls.Load() != 1 {
		t.Fatalf("paused trace reached Tempo: %d, calls=%d", response.Code, backendCalls.Load())
	}
	metrics := httptest.NewRecorder()
	controller.ServeHTTP(metrics, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	body, _ := io.ReadAll(metrics.Result().Body)
	if !strings.Contains(string(body), "rag_observability_trace_ingest_paused 1") {
		t.Fatalf("pause status missing from metrics: %s", body)
	}
	if err := os.Remove(filepath.Join(data, "block")); err != nil {
		t.Fatal(err)
	}
	s = controller.Check()
	if s.Paused || s.RetentionHours != 96 {
		t.Fatalf("capacity recovery did not resume traces: %+v", s)
	}
}

func TestStorageScanFailureFailsClosed(t *testing.T) {
	controller, err := New(Config{DataPath: filepath.Join(t.TempDir(), "missing"), OverrideDir: t.TempDir(), BudgetBytes: 1 << 20, TempoURL: "http://tempo:4318"})
	if err != nil {
		t.Fatal(err)
	}
	s := controller.Check()
	if !s.Paused || s.Error != "STORAGE_SCAN_FAILED" {
		t.Fatalf("unexpected scan failure state: %+v", s)
	}
	response := httptest.NewRecorder()
	controller.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("health endpoint reported healthy on scan failure: %d", response.Code)
	}
}
