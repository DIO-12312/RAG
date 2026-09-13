package agent

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// assertToolPayload 固定 provider 请求层契约：ToolNone 完全不暴露工具，
// ToolAuto 暴露工具并使用 auto，ToolRequired 暴露工具并指向指定函数。
func assertToolPayload(t *testing.T, raw map[string]json.RawMessage, wantTools bool, wantChoice string) {
	t.Helper()

	tools, hasTools := raw["tools"]
	if hasTools != wantTools {
		t.Fatalf("tools presence = %v, want %v (raw=%s)", hasTools, wantTools, tools)
	}

	choice, hasChoice := raw["tool_choice"]
	if wantChoice == "absent" {
		if hasChoice {
			t.Fatalf("tool_choice must be absent for ToolNone, got %s", choice)
		}
		return
	}
	if !hasChoice {
		t.Fatal("tool_choice missing")
	}
	if wantChoice == "auto" {
		var value string
		if json.Unmarshal(choice, &value) != nil || value != "auto" {
			t.Fatalf("tool_choice = %s, want auto", choice)
		}
		return
	}
	var named struct {
		Type     string `json:"type"`
		Function struct {
			Name string `json:"name"`
		} `json:"function"`
	}
	if json.Unmarshal(choice, &named) != nil || named.Type != "function" || named.Function.Name != wantChoice {
		t.Fatalf("tool_choice = %s, want function %s", choice, wantChoice)
	}
}

func TestOpenAIToolPolicyPayloads(t *testing.T) {
	cases := []struct {
		name       string
		policy     ToolPolicy
		wantTools  bool
		wantChoice string
	}{
		{"none", ToolPolicy{Mode: ToolNone}, false, "absent"},
		{"auto", ToolPolicy{Mode: ToolAuto}, true, "auto"},
		{"required", ToolPolicy{Mode: ToolRequired, RequiredName: "rag_retrieve"}, true, "rag_retrieve"},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var raw map[string]json.RawMessage
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if err := json.NewDecoder(r.Body).Decode(&raw); err != nil {
					t.Errorf("decode request: %v", err)
				}
				_ = json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"message": Message{Content: "ok"}}}})
			}))
			defer srv.Close()

			model := OpenAI{BaseURL: srv.URL, Name: "test-model", Timeout: time.Second}
			if _, err := model.Complete(context.Background(), []Message{{Role: "user", Content: "你好"}}, tc.policy); err != nil {
				t.Fatalf("complete failed: %v", err)
			}

			assertToolPayload(t, raw, tc.wantTools, tc.wantChoice)
		})
	}
}

func TestOpenAIStreamRespectsToolPolicy(t *testing.T) {
	cases := []struct {
		name       string
		policy     ToolPolicy
		wantTools  bool
		wantChoice string
	}{
		{"none", ToolPolicy{Mode: ToolNone}, false, "absent"},
		{"required", ToolPolicy{Mode: ToolRequired, RequiredName: "rag_retrieve"}, true, "rag_retrieve"},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var raw map[string]json.RawMessage
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if err := json.NewDecoder(r.Body).Decode(&raw); err != nil {
					t.Errorf("decode request: %v", err)
				}
				w.Header().Set("Content-Type", "text/event-stream")
				_, _ = w.Write([]byte("data: [DONE]\n\n"))
			}))
			defer srv.Close()

			model := OpenAI{BaseURL: srv.URL, Name: "test-model", Timeout: time.Second}
			if err := model.Stream(context.Background(), []Message{{Role: "user", Content: "你好"}}, tc.policy, func(string) error { return nil }, func(ToolCall) error { return nil }); err != nil {
				t.Fatalf("stream failed: %v", err)
			}

			assertToolPayload(t, raw, tc.wantTools, tc.wantChoice)
		})
	}
}

func TestOpenAIStreamReadsSSEContentDeltas(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)

		_, _ = w.Write([]byte("data: {\"choices\":[{\"delta\":{\"content\":\"Hello \"}}]}\n\n"))
		_, _ = w.Write([]byte("data: {\"choices\":[{\"delta\":{\"content\":\"world\"}}]}\n\n"))
		_, _ = w.Write([]byte("data: [DONE]\n\n"))
	}))
	defer srv.Close()

	model := OpenAI{
		BaseURL: srv.URL,
		Name:    "test-model",
		Timeout: time.Second,
	}

	var got string
	err := model.Stream(
		context.Background(),
		[]Message{{Role: "user", Content: "hello"}},
		ToolPolicy{Mode: ToolAuto},
		func(delta string) error {
			got += delta
			return nil
		},
		func(ToolCall) error {
			return nil
		},
	)

	if err != nil {
		t.Fatalf("stream failed: %v", err)
	}

	if got != "Hello world" {
		t.Fatalf("unexpected streamed content: %q", got)
	}
}

func TestOpenAIStreamReassemblesToolCallArguments(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)

		_, _ = w.Write([]byte(`data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","type":"function","function":{"name":"rag_retrieve","arguments":"{\"query\":\"mig"}}]}}]}` + "\n\n"))
		_, _ = w.Write([]byte(`data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"ration\"}"}}]}}]}` + "\n\n"))
		_, _ = w.Write([]byte("data: [DONE]\n\n"))
	}))
	defer srv.Close()

	model := OpenAI{
		BaseURL: srv.URL,
		Name:    "test-model",
		Timeout: time.Second,
	}

	var got ToolCall
	err := model.Stream(
		context.Background(),
		[]Message{{Role: "user", Content: "search migration"}},
		ToolPolicy{Mode: ToolRequired, RequiredName: "rag_retrieve"},
		func(string) error {
			return nil
		},
		func(call ToolCall) error {
			got = call
			return nil
		},
	)

	if err != nil {
		t.Fatalf("stream failed: %v", err)
	}

	if got.ID != "call-1" {
		t.Fatalf("unexpected tool call id: %q", got.ID)
	}

	if got.Function.Name != "rag_retrieve" {
		t.Fatalf("unexpected tool name: %q", got.Function.Name)
	}

	if got.Function.Arguments != `{"query":"migration"}` {
		t.Fatalf("unexpected tool arguments: %q", got.Function.Arguments)
	}
}
