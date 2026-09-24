package observability

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

func TestMetricsUsesFixedQueriesAndReportsPartialResults(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/query_range" || r.URL.Query().Get("step") == "" {
			t.Errorf("unexpected query: %s", r.URL)
		}
		if strings.Contains(r.URL.Query().Get("query"), "rag_outbox_publish_total") {
			http.Error(w, "down", http.StatusServiceUnavailable)
			return
		}
		_, _ = w.Write([]byte(`{"status":"success","data":{"resultType":"matrix","result":[{"metric":{},"values":[[100,"1.5"]]}]}}`))
	}))
	defer server.Close()
	client, err := New(server.URL, "")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := client.Metrics(context.Background(), "1h;delete"); err != ErrInvalidInput {
		t.Fatal(err)
	}
	got, err := client.Metrics(context.Background(), "1h")
	if err != nil {
		t.Fatal(err)
	}
	if got.Status != "partial" || len(got.Panels) != len(metricSpecs) {
		t.Fatalf("%+v", got)
	}
	if got.Panels[len(got.Panels)-1].Code != "PROMETHEUS_QUERY_FAILED" {
		t.Fatal(got.Panels)
	}
}

func TestTraceAllowlistStripsSensitiveAttributes(t *testing.T) {
	traceID := strings.Repeat("a", 32)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v2/traces/"+traceID {
			t.Errorf("unexpected path %q", r.URL.Path)
		}
		_, _ = w.Write([]byte(`{"trace":{"resourceSpans":[{"resource":{"attributes":[{"key":"service.name","value":{"stringValue":"rag-python-worker"}}]},"scopeSpans":[{"spans":[{"spanId":"0123456789abcdef","startTimeUnixNano":"1000000000","endTimeUnixNano":"2000000000","attributes":[{"key":"stage","value":{"stringValue":"embedding"}},{"key":"job.id","value":{"stringValue":"job-123"}},{"key":"prompt","value":{"stringValue":"private question"}}]}]}]}]}}`))
	}))
	defer server.Close()
	client, err := New("", server.URL)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := client.Trace(context.Background(), "../bad"); err != ErrInvalidInput {
		t.Fatal(err)
	}
	got, err := client.Trace(context.Background(), traceID)
	if err != nil {
		t.Fatal(err)
	}
	if got.Status != "ok" || len(got.Spans) != 1 || got.Spans[0].JobID != "job-123" {
		t.Fatalf("%+v", got)
	}
	encoded, _ := json.Marshal(got)
	if strings.Contains(string(encoded), "private question") || strings.Contains(string(encoded), "prompt") {
		t.Fatal(string(encoded))
	}
}

func TestBackendLimitsAndEmptyResults(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/search" {
			_, _ = w.Write([]byte(`{"traces":[]}`))
			return
		}
		_, _ = w.Write([]byte(strings.Repeat("x", responseLimit+1)))
	}))
	defer server.Close()
	client, _ := New(server.URL, server.URL)
	list, err := client.Traces(context.Background(), "rag-go-api", "15m")
	if err != nil || list.Status != "empty" {
		t.Fatalf("%+v %v", list, err)
	}
	if _, err := client.Metrics(context.Background(), "15m"); err != ErrUnavailable {
		t.Fatal(err)
	}
}

func TestLiveQueryBackends(t *testing.T) {
	prometheus, tempo, traceID := os.Getenv("OBS_TEST_PROMETHEUS_URL"), os.Getenv("OBS_TEST_TEMPO_URL"), os.Getenv("OBS_TEST_TRACE_ID")
	if prometheus == "" || tempo == "" || traceID == "" {
		t.Skip("live observability endpoints and trace ID required")
	}
	client, err := New(prometheus, tempo)
	if err != nil {
		t.Fatal(err)
	}
	metrics, err := client.Metrics(context.Background(), "1h")
	if err != nil || len(metrics.Panels) != len(metricSpecs) {
		t.Fatalf("metrics status=%s error=%v", metrics.Status, err)
	}
	traces, err := client.Traces(context.Background(), "rag-go-api", "1h")
	if err != nil || len(traces.Traces) == 0 {
		t.Fatalf("trace list status=%s error=%v", traces.Status, err)
	}
	detail, err := client.Trace(context.Background(), traceID)
	if err != nil || len(detail.Spans) == 0 {
		t.Fatalf("trace detail status=%s error=%v", detail.Status, err)
	}
	services := map[string]bool{}
	for _, span := range detail.Spans {
		services[span.Service] = true
	}
	if !services["rag-go-api"] || !services["rag-python-server"] {
		t.Fatalf("cross-language services missing: %v", services)
	}
}
