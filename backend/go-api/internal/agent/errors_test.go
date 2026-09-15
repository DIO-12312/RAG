package agent

import (
	"context"
	"errors"
	"testing"
)

func TestAgentErrorClassification(t *testing.T) {
	base := errors.New("model unavailable")
	err := NewAgentError(ErrorClassModel, base)

	if got := ErrorClassOf(err); got != ErrorClassModel {
		t.Fatalf("expected model, got %q", got)
	}

	if !errors.Is(err, base) {
		t.Fatal("agent error should unwrap to the original error")
	}
}

func TestErrorClassOfUnknownError(t *testing.T) {
	err := errors.New("unexpected failure")

	if got := ErrorClassOf(err); got != ErrorClassInternal {
		t.Fatalf("expected internal, got %q", got)
	}
}

func TestNewAgentErrorNil(t *testing.T) {
	if got := NewAgentError(ErrorClassModel, nil); got != nil {
		t.Fatal("expected nil error")
	}

	if got := ErrorClassOf(nil); got != "" {
		t.Fatalf("expected empty class, got %q", got)
	}
}

func TestAgentRunClassifiesCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	h := Harness{
		Model: &fixedModel{},
		Tool:  &retriever{},
	}

	_, _, err := h.Run(ctx, "dataset", "测试问题", nil, func(string, any) error {
		return nil
	})

	if got := ErrorClassOf(err); got != ErrorClassCancelled {
		t.Fatalf("expected cancelled, got %q (%v)", got, err)
	}
}

func TestAgentRunClassifiesRetrievalError(t *testing.T) {
	retrievalErr := errors.New("retrieval unavailable")
	h := Harness{
		Model: retrievalErrorModel{},
		Tool:  retrievalErrorRetriever{err: retrievalErr},
	}

	_, _, err := h.Run(context.Background(), "dataset", "测试问题", nil, func(string, any) error {
		return nil
	})

	if got := ErrorClassOf(err); got != ErrorClassRetrieval {
		t.Fatalf("expected retrieval, got %q (%v)", got, err)
	}
}

type retrievalErrorModel struct{}

func (retrievalErrorModel) Complete(context.Context, []Message, bool) (Message, error) {
	call := ToolCall{ID: "call-retrieval-error", Type: "function"}
	call.Function.Name = ragRetrieveToolName
	call.Function.Arguments = `{"query":"测试问题"}`
	return Message{
		Role:      "assistant",
		ToolCalls: []ToolCall{call},
	}, nil
}

type retrievalErrorRetriever struct {
	err error
}

func (r retrievalErrorRetriever) Retrieve(context.Context, string, string, int) ([]Evidence, error) {
	return nil, r.err
}

func TestAgentRunClassifiesModelError(t *testing.T) {
	modelErr := errors.New("model unavailable")

	// fixedModel returns ctx.Err(), so use a small wrapper model
	// to simulate a genuine model failure.
	h := Harness{
		Model: modelErrorModel{err: modelErr},
		Tool:  &retriever{},
	}

	_, _, err := h.Run(context.Background(), "dataset", "测试问题", nil, func(string, any) error {
		return nil
	})

	if got := ErrorClassOf(err); got != ErrorClassModel {
		t.Fatalf("expected model, got %q (%v)", got, err)
	}
}

type modelErrorModel struct {
	err error
}

func (m modelErrorModel) Complete(context.Context, []Message, bool) (Message, error) {
	return Message{}, m.err
}

func TestAgentRunClassifiesToolError(t *testing.T) {
	h := Harness{
		Model: tooManyToolCallsModel{},
		Tool:  &retriever{},
	}

	_, _, err := h.Run(context.Background(), "dataset", "测试问题", nil, func(string, any) error {
		return nil
	})

	if got := ErrorClassOf(err); got != ErrorClassTool {
		t.Fatalf("expected tool, got %q (%v)", got, err)
	}
}

type tooManyToolCallsModel struct{}

func (tooManyToolCallsModel) Complete(context.Context, []Message, bool) (Message, error) {
	calls := make([]ToolCall, 5)
	for i := range calls {
		calls[i] = ToolCall{
			ID:   "call-" + string(rune('1'+i)),
			Type: "function",
		}
		calls[i].Function.Name = ragRetrieveToolName
		calls[i].Function.Arguments = `{"query":"测试问题"}`
	}

	return Message{
		Role:      "assistant",
		ToolCalls: calls,
	}, nil
}

func TestAgentRunClassifiesConvergenceError(t *testing.T) {
	h := Harness{
		Model: loopingToolModel{},
		Tool:  &retriever{},
	}

	_, _, err := h.Run(context.Background(), "dataset", "测试问题", nil, func(string, any) error {
		return nil
	})

	if got := ErrorClassOf(err); got != ErrorClassConvergence {
		t.Fatalf("expected convergence, got %q (%v)", got, err)
	}
}

type loopingToolModel struct{}

func (loopingToolModel) Complete(context.Context, []Message, bool) (Message, error) {
	call := ToolCall{ID: "call-loop", Type: "function"}
	call.Function.Name = ragRetrieveToolName
	call.Function.Arguments = `{"query":"测试问题"}`

	return Message{
		Role:      "assistant",
		ToolCalls: []ToolCall{call},
	}, nil
}
