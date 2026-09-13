package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
)

// runStateMachine 按 phase 驱动一次 Run，直到进入终态；终态不会被重开。
func (h Harness) runStateMachine(ctx context.Context, state *RunState, emit Emit) error {
	for {
		if err := ctx.Err(); err != nil {
			state.MarkFailed(StopReasonCancelled)
			return err
		}

		var err error
		switch state.Phase {
		case RunPhaseRoute:
			err = h.routePhase(state, emit)
		case RunPhaseModel:
			err = h.modelPhase(ctx, state, emit)
		case RunPhaseTool:
			err = h.toolPhase(ctx, state, emit)
		case RunPhaseFinalize:
			err = h.finalizePhase(state, emit)
		default:
			return fmt.Errorf("unsupported run phase %s", state.Phase)
		}
		if err != nil {
			return err
		}
		if state.Stopped() {
			return nil
		}
	}
}

// routePhase 只消费路由结果：澄清直接结束，其余动作进入模型相位。
func (h Harness) routePhase(state *RunState, emit Emit) error {
	if !knownIntentAction(state.Intent.Action) {
		state.MarkFailed(StopReasonInvalidToolCall)
		return fmt.Errorf("unsupported intent action %q", state.Intent.Action)
	}

	if state.Intent.Action == "clarify" {
		if state.Intent.ClarificationQuestion == "" {
			state.MarkFailed(StopReasonClarification)
			return errors.New("clarification question is empty")
		}
		if err := emit("token", map[string]any{"text": state.Intent.ClarificationQuestion}); err != nil {
			state.MarkFailed(StopReasonCancelled)
			return err
		}
		state.Answer = state.Intent.ClarificationQuestion
		state.MarkStopped(StopReasonClarification)
		return nil
	}

	return state.TransitionTo(RunPhaseModel)
}

// modelPhase 在预算内调用模型；没有工具调用时进入 Finalize，否则进入 Tool。
func (h Harness) modelPhase(ctx context.Context, state *RunState, emit Emit) error {
	if err := state.CheckModelCall(); err != nil {
		state.MarkFailed(StopReasonBudgetExceeded)
		return err
	}

	state.Messages = state.Budget.TrimMessages(state.Messages)
	if !state.Budget.Fits(state.Messages) {
		state.MarkFailed(StopReasonBudgetExceeded)
		return errors.New("context budget exceeded")
	}

	policy := PolicyForIntent(state.Intent, state.ModelCalls)

	var msg Message
	if state.Streaming {
		sm, ok := h.Model.(StreamingModel)
		if !ok {
			state.MarkFailed(StopReasonProviderError)
			return errors.New("model does not support streaming")
		}
		var content strings.Builder
		var toolCalls []ToolCall
		streamErr := sm.Stream(
			ctx,
			state.Messages,
			policy,
			func(delta string) error {
				content.WriteString(delta)
				return emit("token", map[string]any{"text": delta})
			},
			func(call ToolCall) error {
				toolCalls = append(toolCalls, call)
				return nil
			},
		)
		if streamErr != nil {
			return failFromError(state, ctx, streamErr)
		}
		msg = Message{Role: "assistant", Content: content.String(), ToolCalls: toolCalls}
	} else {
		completed, err := h.Model.Complete(ctx, state.Messages, policy)
		if err != nil {
			return failFromError(state, ctx, err)
		}
		msg = completed
	}
	state.RecordModelCall()
	msg.Role = "assistant"

	if policy.Mode == ToolNone && len(msg.ToolCalls) > 0 {
		state.MarkFailed(StopReasonInvalidToolCall)
		return errors.New("unexpected tool call for direct reply")
	}

	if len(msg.ToolCalls) == 0 {
		state.Final = msg
		return state.TransitionTo(RunPhaseFinalize)
	}

	if err := state.CheckToolCalls(len(msg.ToolCalls)); err != nil {
		state.MarkFailed(StopReasonBudgetExceeded)
		return err
	}

	state.Messages = append(state.Messages, msg)
	state.ToolCalls = msg.ToolCalls
	return state.TransitionTo(RunPhaseTool)
}

// toolPhase 顺序执行本轮的 rag_retrieve 调用，并保持事件顺序稳定。
func (h Harness) toolPhase(ctx context.Context, state *RunState, emit Emit) error {
	for _, call := range state.ToolCalls {
		if state.Intent.Action == "reuse" {
			state.MarkFailed(StopReasonInvalidToolCall)
			return errors.New("transformation must not call retrieval tool")
		}
		if call.ID == "" || call.Function.Name != "rag_retrieve" {
			state.MarkFailed(StopReasonInvalidToolCall)
			return errors.New("unknown tool")
		}

		var args struct {
			Query string `json:"query"`
		}
		if len(call.Function.Arguments) > 8192 ||
			json.Unmarshal([]byte(call.Function.Arguments), &args) != nil ||
			len(args.Query) == 0 ||
			len(args.Query) > 4096 {
			state.MarkFailed(StopReasonInvalidToolCall)
			return errors.New("invalid tool arguments")
		}

		if state.Intent.Action == "retrieve" && state.Intent.StandaloneQuery != "" {
			args.Query = state.Intent.StandaloneQuery
		}

		if err := state.CheckRetrievalRound(); err != nil {
			state.MarkFailed(StopReasonBudgetExceeded)
			return err
		}

		hits, err := h.Tool.Retrieve(ctx, state.Dataset, args.Query, state.TopK)
		if err != nil {
			return failFromError(state, ctx, err)
		}
		state.RecordRetrievalRound()
		state.AttemptedQueries = append(state.AttemptedQueries, args.Query)

		citations, err := state.Pool.Add(hits)
		if err != nil {
			state.MarkFailed(StopReasonBudgetExceeded)
			return err
		}
		state.Evidence = state.Pool.Citations()

		if err := emit("retrieval", map[string]any{"hits": hits}); err != nil {
			state.MarkFailed(StopReasonCancelled)
			return err
		}

		body, err := state.Pool.EncodeResult(citations)
		if err != nil {
			state.MarkFailed(StopReasonBudgetExceeded)
			return err
		}
		state.Messages = append(state.Messages, Message{Role: "tool", ToolCallID: call.ID, Content: string(body)})
	}

	state.ToolCalls = nil
	return state.TransitionTo(RunPhaseModel)
}

// finalizePhase 校验最终引用并结束 Run；不允许多引用不存在的 Evidence。
func (h Harness) finalizePhase(state *RunState, emit Emit) error {
	citations := state.Pool.Citations()
	allowDirectAnswer := state.Intent.Action == "reply" || state.Intent.Action == "reuse"

	if len(citations) == 0 && state.RetrievalRounds == 0 && !allowDirectAnswer {
		state.MarkFailed(StopReasonInvalidToolCall)
		return errors.New("model did not call retrieval tool")
	}

	valid := []Citation{}
	used := map[int]bool{}
	for _, match := range reference.FindAllStringSubmatch(state.Final.Content, -1) {
		n, _ := strconv.Atoi(match[1])
		if n < 1 || n > len(citations) {
			state.MarkFailed(StopReasonInvalidToolCall)
			return errors.New("model returned unsupported citation")
		}
		if !used[n] {
			valid = append(valid, citations[n-1])
			used[n] = true
		}
	}

	if state.Final.Content == "" {
		state.MarkFailed(StopReasonProviderError)
		return errors.New("model returned empty answer")
	}

	if !state.Streaming {
		if err := emit("token", map[string]any{"text": state.Final.Content}); err != nil {
			state.MarkFailed(StopReasonCancelled)
			return err
		}
	}

	state.Answer = state.Final.Content
	state.Citations = valid
	reason := StopReasonCompleted
	if allowDirectAnswer {
		reason = StopReasonDirectReply
	}
	state.MarkStopped(reason)
	return nil
}

// failFromError 区分取消与供应商错误，并把 Run 置为对应终态。
func failFromError(state *RunState, ctx context.Context, err error) error {
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) || ctx.Err() != nil {
		state.MarkFailed(StopReasonCancelled)
		return err
	}
	state.MarkFailed(StopReasonProviderError)
	return err
}
