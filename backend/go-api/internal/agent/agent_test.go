package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

type retriever struct {
	calls   int
	dataset string
}

func (r *retriever) Retrieve(ctx context.Context, d, q string, k int) ([]Evidence, error) {
	r.calls++
	r.dataset = d
	return []Evidence{{ChunkID: "chunk", DocumentID: "doc", Content: "Migration ends in December.", SourceName: "guide.md"}}, ctx.Err()
}
func TestHTTPToolLoopAndCitation(t *testing.T) {
	for _, thinking := range []bool{false, true} {
		t.Run(fmt.Sprint(thinking), func(t *testing.T) {
			calls := 0
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var body struct {
					Messages []Message `json:"messages"`
					Thinking struct {
						Type string `json:"type"`
					} `json:"thinking"`
					ToolChoice any `json:"tool_choice"`
				}
				if json.NewDecoder(r.Body).Decode(&body) != nil {
					t.Error("bad request")
				}
				calls++
				want := "disabled"
				if thinking {
					want = "enabled"
				}
				if body.Thinking.Type != want {
					t.Error("thinking setting not transmitted")
				}
				if thinking && body.ToolChoice != "auto" {
					t.Error("thinking mode must not force tool_choice")
				}
				msg := Message{Content: "Migration ends in December. [1]"}
				if calls == 1 {
					call := ToolCall{ID: "tool-1", Type: "function"}
					call.Function.Name = "rag_retrieve"
					call.Function.Arguments = `{"query":"migration"}`
					msg = Message{ToolCalls: []ToolCall{call}, ReasoningContent: "test provider context"}
				} else {
					last := body.Messages[len(body.Messages)-1]
					if last.Role != "tool" || last.ToolCallID != "tool-1" {
						t.Error("tool result lost")
					}
					if body.Messages[len(body.Messages)-2].ReasoningContent != "test provider context" {
						t.Error("provider context lost between tool calls")
					}
				}
				_ = json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"message": msg}}})
			}))
			defer srv.Close()
			tool := &retriever{}
			h := Harness{Model: OpenAI{BaseURL: srv.URL, Name: "deepseek-v4-flash", Thinking: thinking, Timeout: time.Second}, Tool: tool}
			events := []string{}
			answer, c, e := h.Run(context.Background(), "owned-dataset", "question", nil, func(event string, _ any) error { events = append(events, event); return nil })
			if e != nil || answer == "" || len(c) != 1 || c[0].Evidence.Content == "" || tool.dataset != "owned-dataset" || calls != 2 {
				t.Fatalf("loop failed: %v", e)
			}
			if len(events) != 2 || events[0] != "retrieval" || events[1] != "token" {
				t.Fatal(events)
			}
		})
	}
}

type fixedModel struct {
	msg   Message
	calls int
}

func (m *fixedModel) Complete(ctx context.Context, _ []Message, _ bool) (Message, error) {
	m.calls++
	return m.msg, ctx.Err()
}
func TestUnknownToolBudgetAndCancellation(t *testing.T) {
	call := ToolCall{ID: "call", Type: "function"}
	call.Function.Name = "delete_document"
	call.Function.Arguments = `{"query":"q"}`
	m := &fixedModel{msg: Message{ToolCalls: []ToolCall{call}}}
	tool := &retriever{}
	h := Harness{Model: m, Tool: tool, MaxRounds: 2}
	emit := func(string, any) error { return nil }
	if _, _, e := h.Run(context.Background(), "owned", "q", nil, emit); e == nil || tool.calls != 0 {
		t.Fatal("unknown tool accepted")
	}
	m.msg.ToolCalls[0].Function.Name = "rag_retrieve"
	m.calls = 0
	if _, _, e := h.Run(context.Background(), "owned", "q", nil, emit); e == nil || m.calls != 2 {
		t.Fatal("budget not enforced")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	m.calls = 0
	if _, _, e := h.Run(ctx, "owned", "q", nil, emit); !errors.Is(e, context.Canceled) || m.calls != 0 {
		t.Fatal("cancellation ignored")
	}
}
