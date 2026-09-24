package observability

import (
	"context"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"math"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
)

const responseLimit = 1 << 20

var (
	ErrUnavailable      = errors.New("observability backend unavailable")
	ErrResponseTooLarge = errors.New("observability backend response too large")
	ErrInvalidInput     = errors.New("invalid observability filter")
	traceIDPattern      = regexp.MustCompile(`^[0-9a-fA-F]{32}$`)
	safeIDPattern       = regexp.MustCompile(`^[0-9a-zA-Z-]{1,64}$`)
	safeCodePattern     = regexp.MustCompile(`^[0-9a-zA-Z_]{1,64}$`)
)

var windows = map[string]time.Duration{
	"15m": 15 * time.Minute,
	"1h":  time.Hour,
	"6h":  6 * time.Hour,
	"24h": 24 * time.Hour,
}

var services = map[string]bool{
	"rag-go-api":        true,
	"rag-python-server": true,
	"rag-python-worker": true,
	"rag-python-outbox": true,
}

func ValidWindow(window string) bool   { _, ok := windows[window]; return ok }
func ValidService(service string) bool { return services[service] }
func ValidTraceID(id string) bool      { return traceIDPattern.MatchString(id) }

type Client struct {
	prometheus string
	tempo      string
	http       *http.Client
}

func New(prometheus, tempo string) (*Client, error) {
	for _, base := range []string{prometheus, tempo} {
		if base == "" {
			continue
		}
		u, err := url.Parse(base)
		if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.Path != "" {
			return nil, ErrInvalidInput
		}
	}
	return &Client{
		prometheus: strings.TrimRight(prometheus, "/"),
		tempo:      strings.TrimRight(tempo, "/"),
		http: &http.Client{
			Timeout:       3 * time.Second,
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		},
	}, nil
}

type Point struct {
	Time  float64 `json:"time"`
	Value float64 `json:"value"`
}

type Series struct {
	Name   string  `json:"name"`
	Points []Point `json:"points"`
}

type Panel struct {
	Key    string   `json:"key"`
	Status string   `json:"status"`
	Code   string   `json:"code,omitempty"`
	Series []Series `json:"series"`
}

type MetricsResponse struct {
	Status string  `json:"status"`
	Window string  `json:"window"`
	Panels []Panel `json:"panels"`
}

type TraceSummary struct {
	TraceID    string  `json:"traceId"`
	Service    string  `json:"service"`
	StartedAt  string  `json:"startedAt"`
	DurationMS float64 `json:"durationMs"`
}

type TraceList struct {
	Status  string         `json:"status"`
	Service string         `json:"service"`
	Window  string         `json:"window"`
	Traces  []TraceSummary `json:"traces"`
}

type Span struct {
	SpanID       string  `json:"spanId"`
	ParentSpanID string  `json:"parentSpanId,omitempty"`
	Service      string  `json:"service"`
	Stage        string  `json:"stage"`
	StartedAt    string  `json:"startedAt"`
	DurationMS   float64 `json:"durationMs"`
	Outcome      string  `json:"outcome"`
	ErrorCode    string  `json:"errorCode,omitempty"`
	RunID        string  `json:"runId,omitempty"`
	JobID        string  `json:"jobId,omitempty"`
	TaskID       string  `json:"taskId,omitempty"`
}

type TraceDetail struct {
	Status    string `json:"status"`
	TraceID   string `json:"traceId"`
	Spans     []Span `json:"spans"`
	Truncated bool   `json:"truncated"`
}

type metricSpec struct {
	key      string
	query    string
	byResult bool
}

var metricSpecs = []metricSpec{
	{"chat_throughput", `sum(rate(rag_chat_runs_total[5m]))`, false},
	{"chat_error_rate", `sum(rate(rag_chat_runs_total{outcome="failed"}[5m])) / clamp_min(sum(rate(rag_chat_runs_total[5m])), 1e-9)`, false},
	{"chat_p50", `histogram_quantile(0.5, sum(rate(rag_chat_duration_seconds_bucket[5m])) by (le))`, false},
	{"chat_p95", `histogram_quantile(0.95, sum(rate(rag_chat_duration_seconds_bucket[5m])) by (le))`, false},
	{"retrieval_p95", `histogram_quantile(0.95, sum(rate(rag_retrieval_duration_seconds_bucket[5m])) by (le))`, false},
	{"grpc_p95", `histogram_quantile(0.95, sum(rate(rag_grpc_client_duration_seconds_bucket[5m])) by (le))`, false},
	{"ingestion_throughput", `sum(rate(rag_ingestion_tasks_total[5m]))`, false},
	{"ingestion_results", `sum by (outcome) (rate(rag_ingestion_tasks_total[5m]))`, true},
	{"outbox_results", `sum by (outcome) (rate(rag_outbox_publish_total[5m]))`, true},
}

func (c *Client) Metrics(ctx context.Context, window string) (MetricsResponse, error) {
	if !ValidWindow(window) {
		return MetricsResponse{}, ErrInvalidInput
	}
	if c == nil || c.prometheus == "" {
		return MetricsResponse{}, ErrUnavailable
	}
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	response := MetricsResponse{Status: "ok", Window: window, Panels: make([]Panel, len(metricSpecs))}
	var wg sync.WaitGroup
	for index, spec := range metricSpecs {
		wg.Add(1)
		go func(index int, spec metricSpec) {
			defer wg.Done()
			series, err := c.queryRange(ctx, window, spec)
			panel := Panel{Key: spec.key, Status: "ok", Series: series}
			if err != nil {
				panel.Status, panel.Code = "unavailable", "PROMETHEUS_QUERY_FAILED"
				panel.Series = []Series{}
			} else if len(series) == 0 {
				panel.Status = "empty"
			}
			response.Panels[index] = panel
		}(index, spec)
	}
	wg.Wait()
	failed, empty := 0, 0
	for _, panel := range response.Panels {
		if panel.Status == "unavailable" {
			failed++
		} else if panel.Status == "empty" {
			empty++
		}
	}
	switch {
	case failed == len(response.Panels):
		return MetricsResponse{}, ErrUnavailable
	case failed > 0:
		response.Status = "partial"
	case empty == len(response.Panels):
		response.Status = "empty"
	}
	return response, nil
}

func (c *Client) queryRange(ctx context.Context, window string, spec metricSpec) ([]Series, error) {
	end := time.Now().UTC()
	start := end.Add(-windows[window])
	step := int(math.Ceil(windows[window].Seconds() / 60))
	values := url.Values{
		"query": {spec.query},
		"start": {strconv.FormatInt(start.Unix(), 10)},
		"end":   {strconv.FormatInt(end.Unix(), 10)},
		"step":  {strconv.Itoa(step)},
	}
	body, status, err := c.get(ctx, c.prometheus+"/api/v1/query_range?"+values.Encode())
	if err != nil || status != http.StatusOK {
		return nil, ErrUnavailable
	}
	var envelope struct {
		Status string `json:"status"`
		Data   struct {
			ResultType string `json:"resultType"`
			Result     []struct {
				Metric map[string]string   `json:"metric"`
				Values [][]json.RawMessage `json:"values"`
			} `json:"result"`
		} `json:"data"`
	}
	if json.Unmarshal(body, &envelope) != nil || envelope.Status != "success" || envelope.Data.ResultType != "matrix" {
		return nil, ErrUnavailable
	}
	series := make([]Series, 0, len(envelope.Data.Result))
	for _, item := range envelope.Data.Result {
		name := "value"
		if spec.byResult {
			name = item.Metric["outcome"]
			if !allowedOutcome(name) {
				continue
			}
		}
		if len(series) >= 8 {
			break
		}
		out := Series{Name: name, Points: []Point{}}
		for _, pair := range item.Values {
			if len(pair) != 2 || len(out.Points) >= 100 {
				continue
			}
			var timestamp float64
			var raw string
			if json.Unmarshal(pair[0], &timestamp) != nil || json.Unmarshal(pair[1], &raw) != nil {
				continue
			}
			value, err := strconv.ParseFloat(raw, 64)
			if err != nil || math.IsNaN(value) || math.IsInf(value, 0) || math.IsNaN(timestamp) || math.IsInf(timestamp, 0) {
				continue
			}
			out.Points = append(out.Points, Point{Time: timestamp, Value: value})
		}
		if len(out.Points) > 0 {
			series = append(series, out)
		}
	}
	return series, nil
}

func (c *Client) Traces(ctx context.Context, service, window string) (TraceList, error) {
	if !ValidService(service) || !ValidWindow(window) {
		return TraceList{}, ErrInvalidInput
	}
	if c == nil || c.tempo == "" {
		return TraceList{}, ErrUnavailable
	}
	end := time.Now().UTC()
	values := url.Values{
		"q":     {`{ resource.service.name = "` + service + `" }`},
		"start": {strconv.FormatInt(end.Add(-windows[window]).Unix(), 10)},
		"end":   {strconv.FormatInt(end.Unix(), 10)},
		"limit": {"100"},
	}
	body, status, err := c.get(ctx, c.tempo+"/api/search?"+values.Encode())
	if err != nil || status != http.StatusOK {
		return TraceList{}, ErrUnavailable
	}
	var envelope struct {
		Traces []struct {
			TraceID           string  `json:"traceID"`
			StartTimeUnixNano string  `json:"startTimeUnixNano"`
			DurationMS        float64 `json:"durationMs"`
		} `json:"traces"`
	}
	if json.Unmarshal(body, &envelope) != nil {
		return TraceList{}, ErrUnavailable
	}
	result := TraceList{Status: "ok", Service: service, Window: window, Traces: []TraceSummary{}}
	for _, item := range envelope.Traces {
		if !ValidTraceID(item.TraceID) || len(result.Traces) >= 100 {
			continue
		}
		result.Traces = append(result.Traces, TraceSummary{
			TraceID: strings.ToLower(item.TraceID), Service: service,
			StartedAt: nanoTimestamp(item.StartTimeUnixNano), DurationMS: finiteDuration(item.DurationMS),
		})
	}
	if len(result.Traces) == 0 {
		result.Status = "empty"
	}
	return result, nil
}

type otlpAttribute struct {
	Key   string `json:"key"`
	Value struct {
		StringValue string `json:"stringValue"`
	} `json:"value"`
}

type otlpSpan struct {
	SpanID            string          `json:"spanId"`
	ParentSpanID      string          `json:"parentSpanId"`
	StartTimeUnixNano string          `json:"startTimeUnixNano"`
	EndTimeUnixNano   string          `json:"endTimeUnixNano"`
	Attributes        []otlpAttribute `json:"attributes"`
	Status            struct {
		Code string `json:"code"`
	} `json:"status"`
}

func (c *Client) Trace(ctx context.Context, traceID string) (TraceDetail, error) {
	if !ValidTraceID(traceID) {
		return TraceDetail{}, ErrInvalidInput
	}
	if c == nil || c.tempo == "" {
		return TraceDetail{}, ErrUnavailable
	}
	body, status, err := c.get(ctx, c.tempo+"/api/v2/traces/"+strings.ToLower(traceID))
	if err != nil {
		return TraceDetail{}, ErrUnavailable
	}
	if status == http.StatusNotFound {
		return TraceDetail{Status: "empty", TraceID: strings.ToLower(traceID), Spans: []Span{}}, nil
	}
	if status != http.StatusOK {
		return TraceDetail{}, ErrUnavailable
	}
	var envelope struct {
		Trace struct {
			ResourceSpans []struct {
				Resource struct {
					Attributes []otlpAttribute `json:"attributes"`
				} `json:"resource"`
				ScopeSpans []struct {
					Spans []otlpSpan `json:"spans"`
				} `json:"scopeSpans"`
			} `json:"resourceSpans"`
		} `json:"trace"`
	}
	if json.Unmarshal(body, &envelope) != nil {
		return TraceDetail{}, ErrUnavailable
	}
	result := TraceDetail{Status: "ok", TraceID: strings.ToLower(traceID), Spans: []Span{}}
	for _, resource := range envelope.Trace.ResourceSpans {
		service := attributeValue(resource.Resource.Attributes, "service.name")
		if !ValidService(service) {
			continue
		}
		for _, scope := range resource.ScopeSpans {
			for _, raw := range scope.Spans {
				if len(result.Spans) >= 200 {
					result.Truncated = true
					break
				}
				spanID := hexID(raw.SpanID, 8)
				if spanID == "" {
					continue
				}
				stage := attributeValue(raw.Attributes, "stage")
				if !safeStage(stage) {
					stage = "rpc"
				}
				outcome := attributeValue(raw.Attributes, "outcome")
				if !allowedOutcome(outcome) {
					outcome = "succeeded"
				}
				if raw.Status.Code == "STATUS_CODE_ERROR" || raw.Status.Code == "2" {
					outcome = "failed"
				}
				start, _ := strconv.ParseUint(raw.StartTimeUnixNano, 10, 64)
				end, _ := strconv.ParseUint(raw.EndTimeUnixNano, 10, 64)
				duration := float64(0)
				if end >= start {
					duration = float64(end-start) / 1e6
				}
				result.Spans = append(result.Spans, Span{
					SpanID: spanID, ParentSpanID: hexID(raw.ParentSpanID, 8),
					Service: service, Stage: stage, StartedAt: nanoTimestamp(raw.StartTimeUnixNano),
					DurationMS: duration, Outcome: outcome,
					ErrorCode: safeCode(attributeValue(raw.Attributes, "error.code")),
					RunID:     safeID(attributeValue(raw.Attributes, "run.id")),
					JobID:     safeID(attributeValue(raw.Attributes, "job.id")),
					TaskID:    safeID(attributeValue(raw.Attributes, "task.id")),
				})
			}
		}
	}
	if len(result.Spans) == 0 {
		result.Status = "empty"
	}
	return result, nil
}

func (c *Client) get(ctx context.Context, address string) ([]byte, int, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, address, nil)
	if err != nil {
		return nil, 0, ErrUnavailable
	}
	response, err := c.http.Do(request)
	if err != nil {
		return nil, 0, ErrUnavailable
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, responseLimit+1))
	if err != nil {
		return nil, 0, ErrUnavailable
	}
	if len(data) > responseLimit {
		return nil, 0, ErrResponseTooLarge
	}
	return data, response.StatusCode, nil
}

func attributeValue(attributes []otlpAttribute, key string) string {
	for _, item := range attributes {
		if item.Key == key {
			return item.Value.StringValue
		}
	}
	return ""
}

func hexID(value string, bytes int) string {
	if value == "" {
		return ""
	}
	if decoded, err := hex.DecodeString(value); err == nil && len(decoded) == bytes {
		return strings.ToLower(value)
	}
	decoded, err := base64.StdEncoding.DecodeString(value)
	if err != nil || len(decoded) != bytes {
		return ""
	}
	return hex.EncodeToString(decoded)
}

func nanoTimestamp(value string) string {
	ns, err := strconv.ParseInt(value, 10, 64)
	if err != nil || ns < 0 {
		return ""
	}
	return time.Unix(0, ns).UTC().Format(time.RFC3339Nano)
}

func finiteDuration(value float64) float64 {
	if math.IsNaN(value) || math.IsInf(value, 0) || value < 0 {
		return 0
	}
	return value
}

func allowedOutcome(value string) bool {
	switch value {
	case "succeeded", "failed", "cancelled", "retry", "skipped":
		return true
	default:
		return false
	}
}

func safeStage(value string) bool {
	switch value {
	case "route", "model", "tool", "assess", "rewrite", "finalize", "complete", "object_read", "parse", "chunk", "embedding", "index", "dense", "sparse", "visibility", "rerank", "evidence", "publish", "rpc", "grpc":
		return true
	default:
		return false
	}
}

func safeID(value string) string {
	if safeIDPattern.MatchString(value) {
		return value
	}
	return ""
}

func safeCode(value string) string {
	if safeCodePattern.MatchString(value) {
		return value
	}
	return ""
}
