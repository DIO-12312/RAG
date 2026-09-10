package agent

import (
	"context"
	"testing"
)

func TestEstimateMessageTokens(t *testing.T) {
	tests := []struct {
		name string
		msg  Message
		want int
	}{
		{
			name: "empty message",
			msg:  Message{Role: "user", Content: ""},
			want: 1,
		},
		{
			name: "ascii content",
			msg:  Message{Role: "user", Content: "12345678901234567890"},
			want: 5,
		},
		{
			name: "short chinese content",
			msg:  Message{Role: "user", Content: "你好世界"},
			want: 2,
		},
		{
			name: "very long ascii content",
			msg:  Message{Role: "user", Content: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
			want: 16,
		},
		{
			name: "tool call arguments are counted",
			msg: Message{
				Role: "assistant",
				ToolCalls: []ToolCall{
					{
						ID:   "call-1",
						Type: "function",
						Function: struct {
							Name      string `json:"name"`
							Arguments string `json:"arguments"`
						}{
							Name:      "rag_retrieve",
							Arguments: `{"query":"hello"}`,
						},
					},
				},
			},
			want: 11,
		},
		{
			name: "tool call id is counted",
			msg:  Message{Role: "tool", ToolCallID: "call-123456"},
			want: 3,
		},
		{
			name: "reasoning content is counted",
			msg:  Message{Role: "assistant", ReasoningContent: "123456789012"},
			want: 3,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := estimateMessageTokens(tt.msg); got != tt.want {
				t.Fatalf("estimateMessageTokens() = %d, want %d", got, tt.want)
			}
		})
	}
}

func TestEstimateContextTokens(t *testing.T) {
	messages := []Message{
		{Role: "system", Content: "system prompt"},
		{Role: "user", Content: "hello"},
		{Role: "assistant", Content: "world"},
	}

	got := estimateContextTokens(messages)

	if got <= 0 {
		t.Fatalf("estimateContextTokens() = %d, want positive value", got)
	}

	want := estimateMessageTokens(messages[0]) +
		estimateMessageTokens(messages[1]) +
		estimateMessageTokens(messages[2])

	if got != want {
		t.Fatalf("estimateContextTokens() = %d, want %d", got, want)
	}
}

func TestContextBudgetFits(t *testing.T) {
	budget := ContextBudget{
		MaxTokens:        100,
		ReserveTokens:    20,
		SystemTokens:     10,
		ToolSchemaTokens: 10,
	}

	messages := []Message{
		{Role: "system", Content: "system"},
		{Role: "user", Content: "hello"},
	}

	if !budget.Fits(messages) {
		t.Fatal("small context should fit")
	}
}

func TestContextBudgetRejectsOverflow(t *testing.T) {
	budget := ContextBudget{
		MaxTokens:        20,
		ReserveTokens:    5,
		SystemTokens:     5,
		ToolSchemaTokens: 5,
	}

	messages := []Message{
		{Role: "user", Content: "1234567890123456789012345678901234567890"},
	}

	if budget.Fits(messages) {
		t.Fatal("large context should not fit")
	}
}

func TestContextBudgetUsage(t *testing.T) {
	budget := ContextBudget{
		MaxTokens:        100,
		ReserveTokens:    20,
		SystemTokens:     10,
		ToolSchemaTokens: 15,
	}

	messages := []Message{
		{Role: "user", Content: "12345678901234567890"},
	}

	want := 10 + 15 + estimateContextTokens(messages)

	if got := budget.UsedTokens(messages); got != want {
		t.Fatalf("UsedTokens() = %d, want %d", got, want)
	}
}

func TestTrimMessagesToBudget(t *testing.T) {
	budget := ContextBudget{
		MaxTokens:        20,
		ReserveTokens:    5,
		SystemTokens:     0,
		ToolSchemaTokens: 0,
	}

	messages := []Message{
		{Role: "user", Content: "old message one 1234567890"},
		{Role: "assistant", Content: "old answer two 1234567890"},
		{Role: "user", Content: "latest question"},
	}

	trimmed := budget.TrimMessages(messages)

	if len(trimmed) >= len(messages) {
		t.Fatalf("TrimMessages() kept too many messages: got %d, want fewer than %d",
			len(trimmed), len(messages))
	}

	if len(trimmed) == 0 {
		t.Fatal("TrimMessages() removed the latest message")
	}

	if trimmed[len(trimmed)-1].Content != "latest question" {
		t.Fatal("TrimMessages() removed the latest message")
	}

	if !budget.Fits(trimmed) {
		t.Fatal("trimmed messages still exceed context budget")
	}
}

func TestDefaultContextBudget(t *testing.T) {
	h := Harness{}

	budget := h.ContextBudget()

	if budget.MaxTokens <= 0 {
		t.Fatal("default max context tokens must be positive")
	}

	if budget.ReserveTokens <= 0 {
		t.Fatal("default reserve tokens must be positive")
	}

	if budget.ReserveTokens >= budget.MaxTokens {
		t.Fatal("reserve tokens must be smaller than max context tokens")
	}
}

type captureRetriever struct{}

func (captureRetriever) Retrieve(context.Context, string, string, int) ([]Evidence, error) {
	return []Evidence{
		{
			ChunkID:    "chunk-1",
			DocumentID: "doc-1",
			Content:    "test evidence",
			SourceName: "test",
			Locator:    "test",
		},
	}, nil
}

type captureModel struct {
	messages []Message
	history  [][]Message
	calls    int
}

func (m *captureModel) Complete(ctx context.Context, messages []Message, _ bool) (Message, error) {
	m.messages = append([]Message(nil), messages...)
	m.history = append(m.history, append([]Message(nil), messages...))
	m.calls++

	if m.calls == 1 {
		return Message{
			ToolCalls: []ToolCall{
				{
					ID:   "call-1",
					Type: "function",
					Function: struct {
						Name      string `json:"name"`
						Arguments string `json:"arguments"`
					}{
						Name:      "rag_retrieve",
						Arguments: `{"query":"latest question"}`,
					},
				},
			},
		}, ctx.Err()
	}

	return Message{
		Content: "answer",
	}, ctx.Err()
}

func TestHarnessAppliesContextBudget(t *testing.T) {
	model := &captureModel{}

	history := []Message{
		{Role: "user", Content: "old question 12345678901234567890"},
		{Role: "assistant", Content: "old answer 12345678901234567890"},
		{Role: "user", Content: "another old question 12345678901234567890"},
		{Role: "assistant", Content: "another old answer 12345678901234567890"},
		{Role: "user", Content: "latest question"},
	}

	budget := ContextBudget{
		MaxTokens:        160,
		ReserveTokens:    2,
		SystemTokens:     2,
		ToolSchemaTokens: 2,
	}

	h := Harness{
		Model:     model,
		MaxRounds: 2,
		TopK:      6,
		Tool:      captureRetriever{},
		Budget:    &budget,
	}

	_, _, err := h.Run(
		context.Background(),
		"owned",
		"latest question",
		history,
		func(string, any) error { return nil },
	)

	if err != nil {
		t.Fatalf("Run() failed: %v", err)
	}

	if len(model.messages) >= len(history)+2 {
		t.Fatalf(
			"context was not trimmed: model received %d messages",
			len(model.messages),
		)
	}

	foundLatest := false
	for _, msg := range model.messages {
		if msg.Role == "user" && msg.Content == "latest question" {
			foundLatest = true
			break
		}
	}
	if !foundLatest {
		t.Fatal("latest question was not preserved")
	}

	if len(model.messages) == 0 || model.messages[0].Role != "system" {
		t.Fatal("system message was not preserved")
	}

	if len(model.history) < 2 {
		t.Fatal("expected at least two model calls")
	}

	t.Logf("first call used=%d tokens", budget.UsedTokens(model.history[0]))
	t.Logf("second call used=%d tokens", budget.UsedTokens(model.history[1]))
	for i, msg := range model.history[1] {
		t.Logf("second[%d]: role=%q content_len=%d tool_calls=%d tool_call_id=%q",
			i, msg.Role, len(msg.Content), len(msg.ToolCalls), msg.ToolCallID)
	}

	if budget.Fits(model.history[1]) == false {
		t.Logf("second model context: used=%d max=%d", budget.UsedTokens(model.history[1]), budget.MaxTokens)
		for i, msg := range model.history[1] {
			t.Logf("message[%d]: role=%q content_len=%d tool_calls=%d tool_call_id=%q",
				i, msg.Role, len(msg.Content), len(msg.ToolCalls), msg.ToolCallID)
		}
		t.Fatalf(
			"second model context exceeds budget: used=%d",
			budget.UsedTokens(model.history[1]),
		)
	}
}

func TestTrimMessagesPreservesToolCallPair(t *testing.T) {
	messages := []Message{
		{Role: "system", Content: "system"},
		{Role: "user", Content: "old question"},
		{Role: "assistant", Content: "old answer"},
		{Role: "user", Content: "latest question"},
		{
			Role: "assistant",
			ToolCalls: []ToolCall{
				{
					ID:   "call-1",
					Type: "function",
					Function: struct {
						Name      string `json:"name"`
						Arguments string `json:"arguments"`
					}{
						Name:      "rag_retrieve",
						Arguments: `{"query":"latest question"}`,
					},
				},
			},
		},
		{
			Role:       "tool",
			ToolCallID: "call-1",
			Content:    "retrieved evidence",
		},
	}

	budget := ContextBudget{
		MaxTokens:        32,
		ReserveTokens:    2,
		SystemTokens:     2,
		ToolSchemaTokens: 2,
	}

	trimmed := budget.TrimMessages(messages)

	if !budget.Fits(trimmed) {
		t.Fatalf(
			"trimmed context still exceeds budget: used=%d max=%d",
			budget.UsedTokens(trimmed),
			budget.MaxTokens,
		)
	}

	if len(trimmed) < 4 {
		t.Fatalf("tool interaction was removed: got %d messages", len(trimmed))
	}

	if trimmed[0].Role != "system" {
		t.Fatal("system message was not preserved")
	}

	if trimmed[1].Role != "user" || trimmed[1].Content != "latest question" {
		t.Logf("trimmed messages: len=%d used=%d", len(trimmed), budget.UsedTokens(trimmed))
		for i, msg := range trimmed {
			t.Logf("trimmed[%d]: role=%q content=%q tool_calls=%d tool_call_id=%q",
				i, msg.Role, msg.Content, len(msg.ToolCalls), msg.ToolCallID)
		}
		t.Fatal("latest user question was not preserved")
	}

	if trimmed[len(trimmed)-2].Role != "assistant" ||
		len(trimmed[len(trimmed)-2].ToolCalls) != 1 {
		t.Fatal("assistant tool call was not preserved")
	}

	if trimmed[len(trimmed)-1].Role != "tool" ||
		trimmed[len(trimmed)-1].ToolCallID != "call-1" {
		t.Fatal("tool result was not preserved with its tool call")
	}
}
