package agent

import (
	"context"
	"errors"
	"fmt"
	"testing"
)

// scriptedModel 按调用顺序返回预设消息，并记录每次收到的工具策略。
type scriptedModel struct {
	responses []Message
	calls     int
	policies  []ToolPolicy
}

func (m *scriptedModel) Complete(ctx context.Context, _ []Message, policy ToolPolicy) (Message, error) {
	m.policies = append(m.policies, policy)
	m.calls++
	if m.calls <= len(m.responses) {
		return m.responses[m.calls-1], ctx.Err()
	}
	return Message{}, fmt.Errorf("unexpected model call %d", m.calls)
}

func toolCallMessage(id, query string) Message {
	call := ToolCall{ID: id, Type: "function"}
	call.Function.Name = "rag_retrieve"
	call.Function.Arguments = fmt.Sprintf(`{"query":%q}`, query)
	return Message{ToolCalls: []ToolCall{call}}
}

func runScripted(t *testing.T, h Harness, question string) (*RunState, error) {
	t.Helper()
	state := h.newRunState("owned-dataset", question, nil)
	err := h.runStateMachine(context.Background(), state, func(string, any) error { return nil })
	return state, err
}

func TestRuntimeRetrievalAnswerConverges(t *testing.T) {
	model := &scriptedModel{responses: []Message{
		toolCallMessage("call-1", "migration"),
		{Content: "Migration ends in December. [1]"},
	}}
	tool := &retriever{}
	h := Harness{Model: model, Tool: tool}

	state, err := runScripted(t, h, "question")
	if err != nil {
		t.Fatalf("run failed: %v", err)
	}
	if model.calls != 2 || tool.calls != 1 {
		t.Fatalf("unexpected call counts: model=%d tool=%d", model.calls, tool.calls)
	}
	if state.StopReason != StopReasonCompleted || state.Phase != RunPhaseDone {
		t.Fatalf("unexpected stop state: %+v", state)
	}
	if state.Answer != "Migration ends in December. [1]" || len(state.Citations) != 1 {
		t.Fatalf("unexpected answer/citations: %q %+v", state.Answer, state.Citations)
	}
	if len(model.policies) != 2 || model.policies[0].Mode != ToolRequired || model.policies[1].Mode != ToolAuto {
		t.Fatalf("unexpected tool policies: %+v", model.policies)
	}
}

func TestRuntimeDirectReplyStopsWithoutRetrieval(t *testing.T) {
	model := &scriptedModel{responses: []Message{{Content: "你好，有什么可以帮你？"}}}
	tool := &retriever{}
	h := Harness{Model: model, Tool: tool}

	state, err := runScripted(t, h, "你好")
	if err != nil {
		t.Fatalf("run failed: %v", err)
	}
	if tool.calls != 0 || state.StopReason != StopReasonDirectReply {
		t.Fatalf("direct reply must not retrieve: %+v", state)
	}
	if len(state.Citations) != 0 {
		t.Fatalf("direct reply must not invent citations: %+v", state.Citations)
	}
}

func TestRuntimeClarificationSkipsModelAndTool(t *testing.T) {
	model := &scriptedModel{}
	tool := &retriever{}
	h := Harness{Model: model, Tool: tool}

	var events []string
	state := h.newRunState("ds", "那它的最大值呢？", nil)
	if err := h.runStateMachine(context.Background(), state, func(event string, _ any) error {
		events = append(events, event)
		return nil
	}); err != nil {
		t.Fatalf("clarification failed: %v", err)
	}
	if model.calls != 0 || tool.calls != 0 {
		t.Fatalf("clarification must not call model or tool: model=%d tool=%d", model.calls, tool.calls)
	}
	if state.StopReason != StopReasonClarification || state.Phase != RunPhaseDone {
		t.Fatalf("unexpected stop state: %+v", state)
	}
	if len(events) != 1 || events[0] != "token" {
		t.Fatalf("unexpected events: %v", events)
	}
}

func TestRuntimeUnknownToolAndInvalidArgumentsFailClosed(t *testing.T) {
	cases := []struct {
		name string
		msg  Message
	}{
		{"unknown tool", Message{ToolCalls: []ToolCall{{ID: "c1", Type: "function", Function: struct {
			Name      string `json:"name"`
			Arguments string `json:"arguments"`
		}{Name: "delete_document", Arguments: `{"query":"q"}`}}}}},
		{"invalid arguments", toolCallMessage("c1", "")},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			model := &scriptedModel{responses: []Message{tc.msg}}
			tool := &retriever{}
			h := Harness{Model: model, Tool: tool}

			state, err := runScripted(t, h, "question")
			if err == nil {
				t.Fatal("invalid tool call must fail")
			}
			if tool.calls != 0 {
				t.Fatalf("invalid tool call must not reach the retriever: %d", tool.calls)
			}
			if state.StopReason != StopReasonInvalidToolCall || state.Phase != RunPhaseFailed {
				t.Fatalf("unexpected stop state: %+v", state)
			}
		})
	}
}

func TestRuntimeBudgetAndCancellationAreTerminal(t *testing.T) {
	t.Run("model budget", func(t *testing.T) {
		model := &scriptedModel{responses: []Message{
			toolCallMessage("call-1", "q1"),
			toolCallMessage("call-2", "q2"),
			toolCallMessage("call-3", "q3"),
		}}
		tool := &retriever{}
		limits := DefaultRunLimits()
		limits.MaxModelCalls = 2
		h := Harness{Model: model, Tool: tool, Limits: limits}

		state, err := runScripted(t, h, "question")
		if err == nil || !errors.Is(err, ErrBudgetExceeded) {
			t.Fatalf("expected budget error, got %v", err)
		}
		if state.StopReason != StopReasonBudgetExceeded || model.calls != 2 {
			t.Fatalf("budget must stop before the third call: %+v model=%d", state, model.calls)
		}
	})

	t.Run("retrieval round budget", func(t *testing.T) {
		model := &scriptedModel{responses: []Message{
			toolCallMessage("call-1", "q1"),
			toolCallMessage("call-2", "q2"),
		}}
		tool := &retriever{}
		limits := DefaultRunLimits()
		limits.MaxRetrievalRounds = 1
		h := Harness{Model: model, Tool: tool, Limits: limits}

		state, err := runScripted(t, h, "question")
		if err == nil || !errors.Is(err, ErrBudgetExceeded) {
			t.Fatalf("expected retrieval budget error, got %v", err)
		}
		if tool.calls != 1 || state.StopReason != StopReasonBudgetExceeded {
			t.Fatalf("retrieval must stop after one round: tool=%d %+v", tool.calls, state)
		}
	})

	t.Run("client cancellation", func(t *testing.T) {
		model := &scriptedModel{responses: []Message{{Content: "unused"}}}
		tool := &retriever{}
		h := Harness{Model: model, Tool: tool}

		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		state := h.newRunState("ds", "question", nil)
		err := h.runStateMachine(ctx, state, func(string, any) error { return nil })
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("expected cancellation, got %v", err)
		}
		if model.calls != 0 || tool.calls != 0 {
			t.Fatalf("cancelled run must not call downstream: model=%d tool=%d", model.calls, tool.calls)
		}
		if state.StopReason != StopReasonCancelled || state.Phase != RunPhaseFailed {
			t.Fatalf("unexpected stop state: %+v", state)
		}
	})
}

func TestRuntimeRejectsUnsupportedCitation(t *testing.T) {
	model := &scriptedModel{responses: []Message{
		toolCallMessage("call-1", "migration"),
		{Content: "Answer without evidence. [3]"},
	}}
	tool := &retriever{}
	h := Harness{Model: model, Tool: tool}

	state, err := runScripted(t, h, "question")
	if err == nil {
		t.Fatal("unsupported citation must be rejected")
	}
	if state.Phase != RunPhaseFailed {
		t.Fatalf("unsupported citation must fail the run: %+v", state)
	}
}

func TestRuntimeDuplicateEvidenceKeepsCitationOrdinals(t *testing.T) {
	model := &scriptedModel{responses: []Message{
		toolCallMessage("call-1", "q1"),
		toolCallMessage("call-2", "q2"),
		{Content: "Same evidence twice. [1]"},
	}}
	tool := &retriever{}
	h := Harness{Model: model, Tool: tool}

	state, err := runScripted(t, h, "question")
	if err != nil {
		t.Fatalf("run failed: %v", err)
	}
	if tool.calls != 2 {
		t.Fatalf("expected two retrievals, got %d", tool.calls)
	}
	if len(state.Citations) != 1 || state.Citations[0].Ordinal != 1 {
		t.Fatalf("duplicate evidence must keep a single stable ordinal: %+v", state.Citations)
	}
}

func TestRuntimeContextBudgetFailure(t *testing.T) {
	model := &scriptedModel{}
	tool := &retriever{}
	h := Harness{
		Model:  model,
		Tool:   tool,
		Budget: &ContextBudget{MaxTokens: 8, ReserveTokens: 1, SystemTokens: 1, ToolSchemaTokens: 1},
	}
	question := ""
	for i := 0; i < 400; i++ {
		question += "context "
	}

	state, err := runScripted(t, h, question)
	if err == nil {
		t.Fatal("context budget overflow must fail")
	}
	if state.StopReason != StopReasonBudgetExceeded || model.calls != 0 {
		t.Fatalf("unexpected stop state: %+v model=%d", state, model.calls)
	}
}
