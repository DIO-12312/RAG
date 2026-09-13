package agent

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

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
		false,
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
		true,
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
