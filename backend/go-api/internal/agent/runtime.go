package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"
)

// observe 发送脱敏事件；Observer 自身 panic 不得影响 Run 结果。
func (h Harness) observe(ctx context.Context, state *RunState, event RunEvent) {
	if h.Observer == nil {
		return
	}
	event.RunID = h.RunID
	event.ModelCalls = state.ModelCalls
	event.RetrievalCalls = state.RetrievalRounds
	event.RewriteCalls = state.RewriteRounds
	event.EvidenceCount = state.Pool.Len()
	defer func() { _ = recover() }()
	h.Observer.Observe(ctx, event)
}

// runStateMachine 按 phase 驱动一次 Run，并在所有退出路径上只发送一个终态事件。
func (h Harness) runStateMachine(ctx context.Context, state *RunState, emit Emit) error {
	started := time.Now()
	err := h.runPhases(ctx, state, emit)
	if err != nil && state.Phase == RunPhaseFailed {
		err = &RunError{Reason: state.StopReason, Err: err}
	}

	code := errorCodeForStopReason(state.StopReason)
	if code == "" && err != nil {
		code = "run_failed"
	}
	h.observe(ctx, state, RunEvent{
		Stage:      RunStageComplete,
		DurationMS: time.Since(started).Milliseconds(),
		ErrorCode:  code,
		StopReason: state.StopReason,
	})
	return err
}

// runPhases 执行相位循环，直到进入终态或返回错误。
func (h Harness) runPhases(ctx context.Context, state *RunState, emit Emit) error {
	for {
		if err := ctx.Err(); err != nil {
			state.MarkFailed(StopReasonCancelled)
			return err
		}

		var err error
		switch state.Phase {
		case RunPhaseRoute:
			err = h.routePhase(ctx, state, emit)
		case RunPhaseModel:
			err = h.modelPhase(ctx, state, emit)
		case RunPhaseTool:
			err = h.toolPhase(ctx, state, emit)
		case RunPhaseAssess:
			err = h.assessPhase(ctx, state, emit)
		case RunPhaseRewrite:
			err = h.rewritePhase(ctx, state, emit)
		case RunPhaseFinalize:
			err = h.finalizePhase(ctx, state, emit)
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
func (h Harness) routePhase(ctx context.Context, state *RunState, emit Emit) error {
	if !knownIntentAction(state.Intent.Action) {
		state.MarkFailed(StopReasonInvalidToolCall)
		return fmt.Errorf("unsupported intent action %q", state.Intent.Action)
	}
	h.observe(ctx, state, RunEvent{Stage: RunStageRoute, Action: state.Intent.Action})

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

// modelPhase 调用模型；没有工具调用时进入 Finalize，否则进入 Tool。
func (h Harness) modelPhase(ctx context.Context, state *RunState, emit Emit) error {
	state.Messages = state.Budget.TrimMessages(state.Messages)
	if !state.Budget.Fits(state.Messages) {
		state.MarkFailed(StopReasonBudgetExceeded)
		return errors.New("context budget exceeded")
	}
	if err := h.emitContext(state, emit); err != nil {
		return err
	}

	policy := PolicyForIntent(state.Intent, state.ModelCalls)

	msg, err := h.complete(ctx, state, state.Messages, policy, emit)
	if err != nil {
		return failFromError(state, ctx, err)
	}
	state.RecordModelCall()
	h.observe(ctx, state, RunEvent{Stage: RunStageModel, Round: state.ModelCalls, Action: string(policy.Mode)})

	if policy.Mode == ToolNone && len(msg.ToolCalls) > 0 {
		state.MarkFailed(StopReasonInvalidToolCall)
		return errors.New("unexpected tool call for direct reply")
	}

	if len(msg.ToolCalls) == 0 {
		state.Final = msg
		state.AnswerNeeded = false
		return state.TransitionTo(RunPhaseFinalize)
	}

	if err := state.CheckToolCalls(len(msg.ToolCalls)); err != nil {
		// 只执行模型给出的前 N 个调用，避免单轮工具数量异常导致整次问答失败。
		// 同时裁剪写入消息历史的 assistant tool_calls，保持调用与结果严格成对。
		msg.ToolCalls = append([]ToolCall(nil), msg.ToolCalls[:state.Limits.MaxToolCallsPerRound]...)
	}

	state.Messages = append(state.Messages, msg)
	state.ToolCalls = msg.ToolCalls
	state.ToolReason = "model"
	return state.TransitionTo(RunPhaseTool)
}

// toolPhase 顺序执行本轮的 rag_retrieve 调用，并保持事件顺序稳定。
func (h Harness) toolPhase(ctx context.Context, state *RunState, emit Emit) error {
	for index, call := range state.ToolCalls {
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

		// 首次检索使用路由得到的独立问题；后续轮次必须使用模型或重写器给出的查询。
		if state.Intent.Action == "retrieve" && state.Intent.StandaloneQuery != "" && state.RetrievalRounds == 0 {
			args.Query = state.Intent.StandaloneQuery
		}

		if state.queryAttempted(args.Query) {
			// 重复查询既不调用 Retriever 也不消耗检索轮次。补齐 tool result 后立即
			// 收敛到受限回答，避免取消模型调用次数上限后模型反复提交同一查询。
			exhaustRetrievalBudget(state, state.ToolCalls[index:])
			h.observe(ctx, state, RunEvent{Stage: RunStageTool, Action: "duplicate_query"})
			return state.TransitionTo(RunPhaseFinalize)
		}

		if state.RetrievalRounds >= state.Limits.MaxRetrievalRounds {
			// 检索轮次用尽不是失败：补齐剩余工具结果并带"证据不足"约束收尾，
			// 否则用户只会看到笼统的 CHAT_FAILED。
			exhaustRetrievalBudget(state, state.ToolCalls[index:])
			h.observe(ctx, state, RunEvent{Stage: RunStageTool, Action: "budget_exhausted"})
			return state.TransitionTo(RunPhaseFinalize)
		}

		hits, err := h.Tool.Retrieve(ctx, state.Dataset, args.Query, state.TopK)
		if err != nil {
			return failFromError(state, ctx, err)
		}
		retrievalRound := state.RetrievalRounds + 1
		state.RecordRetrievalRound()
		state.AttemptedQueries = append(state.AttemptedQueries, args.Query)

		citations, err := state.Pool.Add(hits)
		if err != nil {
			state.MarkFailed(StopReasonBudgetExceeded)
			return err
		}
		state.Evidence = state.Pool.Citations()

		reason := state.ToolReason
		if reason == "" {
			reason = "initial"
		}
		queryHash, _ := QueryFingerprint(args.Query)
		h.observe(ctx, state, RunEvent{Stage: RunStageTool, Round: retrievalRound, Action: reason, QueryHash: queryHash})

		if err := emit("retrieval", map[string]any{"hits": hits, "round": retrievalRound, "reason": reason}); err != nil {
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
	state.ToolReason = ""
	return state.TransitionTo(RunPhaseAssess)
}

// assessPhase 执行结构化充分性判断。
//
// 未配置 SCA 时保持既有"模型自省"循环；SCA 失败（ErrSufficiencyUnavailable）时
// 停止额外检索并把受限回答交给 Finalize，取消与预算错误立即终止。
func (h Harness) assessPhase(ctx context.Context, state *RunState, emit Emit) error {
	if h.Assessor == nil {
		state.AnswerNeeded = false
		return state.TransitionTo(RunPhaseModel)
	}

	decision, err := h.Assessor.Assess(ctx, state.Question, state.Pool.Citations())
	h.observe(ctx, state, RunEvent{Stage: RunStageAssess, Round: state.RetrievalRounds, Action: assessAction(decision, err)})
	if err != nil {
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) || ctx.Err() != nil {
			state.MarkFailed(StopReasonCancelled)
			return err
		}
		if !errors.Is(err, ErrSufficiencyUnavailable) {
			state.MarkFailed(StopReasonProviderError)
			return err
		}
		// 降级：不再额外检索，交给 Finalize 并要求说明证据不足。
		state.Sufficiency = SufficiencyDecision{Sufficient: false, ReasonCode: "assessor_unavailable"}
		state.SufficiencyChecked = true
		state.AnswerNeeded = true
		return state.TransitionTo(RunPhaseFinalize)
	}

	state.RecordModelCall()
	state.Sufficiency = decision
	state.SufficiencyChecked = true
	state.AnswerNeeded = true

	if decision.Sufficient {
		return state.TransitionTo(RunPhaseFinalize)
	}

	if h.Rewriter != nil && state.CheckRewriteRound() == nil {
		return state.TransitionTo(RunPhaseRewrite)
	}
	// 没有重写器或改写额度已用尽：带不足约束收尾。
	return state.TransitionTo(RunPhaseFinalize)
}

// rewritePhase 依据缺口生成新查询，并把它们表达为合成的 rag_retrieve 工具调用，
// 使 tool call/result 始终成对、检索事件顺序稳定。
func (h Harness) rewritePhase(ctx context.Context, state *RunState, emit Emit) error {
	standalone := state.Intent.StandaloneQuery
	if len(state.AttemptedQueries) > 0 {
		standalone = state.AttemptedQueries[0]
	}
	request := RewriteRequest{
		OriginalQuestion:   state.Question,
		StandaloneQuestion: standalone,
		MissingFacts:       append([]string(nil), state.Sufficiency.MissingFacts...),
		AttemptedQueries:   append([]string(nil), state.AttemptedQueries...),
	}

	result, err := h.Rewriter.Rewrite(ctx, request)
	rewriteAction := "rewritten"
	rewriteHash := ""
	if err != nil {
		rewriteAction = "failed"
		if errors.Is(err, ErrNoNewQuery) {
			rewriteAction = "no_new_query"
		} else if errors.Is(err, ErrRewriteUnavailable) {
			rewriteAction = "unavailable"
		}
	} else if len(result.Queries) > 0 {
		rewriteHash, _ = QueryFingerprint(result.Queries[0])
	}
	h.observe(ctx, state, RunEvent{Stage: RunStageRewrite, Round: state.RewriteRounds + 1, Action: rewriteAction, QueryHash: rewriteHash})
	if err != nil {
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) || ctx.Err() != nil {
			state.MarkFailed(StopReasonCancelled)
			return err
		}
		state.RecordModelCall()
		if errors.Is(err, ErrNoNewQuery) || errors.Is(err, ErrRewriteUnavailable) {
			state.AnswerNeeded = true
			return state.TransitionTo(RunPhaseFinalize)
		}
		state.MarkFailed(StopReasonProviderError)
		return err
	}

	state.RecordModelCall()
	state.RecordRewriteRound()

	round := state.RewriteRounds
	calls := make([]ToolCall, 0, len(result.Queries))
	for index, query := range result.Queries {
		encoded, encodeErr := json.Marshal(query)
		if encodeErr != nil {
			state.MarkFailed(StopReasonProviderError)
			return errors.New("rewritten query cannot be encoded")
		}
		call := ToolCall{ID: fmt.Sprintf("rewrite-%d-%d", round, index+1), Type: "function"}
		call.Function.Name = "rag_retrieve"
		call.Function.Arguments = fmt.Sprintf(`{"query":%s}`, encoded)
		calls = append(calls, call)
	}

	state.ToolCalls = calls
	state.ToolReason = "rewrite"
	state.Messages = append(state.Messages, Message{Role: "assistant", ToolCalls: append([]ToolCall(nil), calls...)})
	return state.TransitionTo(RunPhaseTool)
}

// complete 统一处理流式与非流式模型调用，并在流式路径转发 token 事件。
func (h Harness) complete(ctx context.Context, state *RunState, messages []Message, policy ToolPolicy, emit Emit) (Message, error) {
	if !state.Streaming {
		msg, err := h.Model.Complete(ctx, messages, policy)
		if err != nil {
			return Message{}, err
		}
		msg.Role = "assistant"
		return msg, nil
	}

	sm, ok := h.Model.(StreamingModel)
	if !ok {
		return Message{}, errors.New("model does not support streaming")
	}
	var content strings.Builder
	var toolCalls []ToolCall
	if err := sm.Stream(
		ctx,
		messages,
		policy,
		func(delta string) error {
			content.WriteString(delta)
			return emit("token", map[string]any{"text": delta})
		},
		func(call ToolCall) error {
			toolCalls = append(toolCalls, call)
			return nil
		},
	); err != nil {
		return Message{}, err
	}
	return Message{Role: "assistant", Content: content.String(), ToolCalls: toolCalls}, nil
}

// finalizePhase 校验最终引用并结束 Run；不允许多引用不存在的 Evidence。
func (h Harness) finalizePhase(ctx context.Context, state *RunState, emit Emit) error {
	citations := state.Pool.Citations()
	allowDirectAnswer := state.Intent.Action == "reply" || state.Intent.Action == "reuse"

	if state.AnswerNeeded {
		messages := state.Messages
		if state.SufficiencyChecked && !state.Sufficiency.Sufficient {
			messages = withSystemDirective(messages, insufficientEvidenceDirective)
		}
		messages = state.Budget.TrimMessages(messages)
		if !state.Budget.Fits(messages) {
			state.MarkFailed(StopReasonBudgetExceeded)
			return errors.New("context budget exceeded")
		}
		msg, err := h.complete(ctx, state, messages, ToolPolicy{Mode: ToolNone}, emit)
		if err != nil {
			return failFromError(state, ctx, err)
		}
		state.RecordModelCall()
		state.AnswerNeeded = false
		state.Final = msg
	}

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
	h.observe(ctx, state, RunEvent{Stage: RunStageFinalize, Round: state.RetrievalRounds, Action: string(state.StopReason)})

	reason := StopReasonCompleted
	switch {
	case state.SufficiencyChecked && state.Sufficiency.Sufficient:
		reason = StopReasonEvidenceSufficient
	case state.SufficiencyChecked:
		reason = StopReasonEvidenceInsufficient
	case allowDirectAnswer:
		reason = StopReasonDirectReply
	}
	state.MarkStopped(reason)
	return nil
}

// insufficientEvidenceDirective 在 SCA 判定（或降级为）证据不足时约束最终回答。
const insufficientEvidenceDirective = "The retrieved evidence is insufficient to answer fully. " +
	"State clearly which facts are missing, do not invent citations, and do not answer beyond the supplied evidence."

// withSystemDirective 保持消息顺序不变，只扩展首条 system 提示。
func withSystemDirective(messages []Message, directive string) []Message {
	if len(messages) == 0 {
		return []Message{{Role: "system", Content: directive}}
	}
	updated := append([]Message(nil), messages...)
	updated[0] = Message{Role: "system", Content: strings.TrimSpace(messages[0].Content + "\n\n" + directive)}
	return updated
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

// assessAction 把 SCA 结果映射为稳定的观测动作名。
func assessAction(decision SufficiencyDecision, err error) string {
	if err != nil {
		if errors.Is(err, ErrSufficiencyUnavailable) {
			return "unavailable"
		}
		return "failed"
	}
	if decision.Sufficient {
		return "sufficient"
	}
	return "insufficient"
}

// exhaustRetrievalBudget 在检索轮次预算用尽时补齐未执行工具调用的结果，
// 并把 Run 标记为证据不足，交给 Finalize 生成带约束的回答。
func exhaustRetrievalBudget(state *RunState, remaining []ToolCall) {
	for _, pending := range remaining {
		state.Messages = append(state.Messages, Message{
			Role:       "tool",
			ToolCallID: pending.ID,
			Content:    `{"note":"retrieval budget exhausted"}`,
		})
	}

	decision := state.Sufficiency
	decision.Sufficient = false
	if decision.ReasonCode == "" || decision.ReasonCode == "covered" {
		decision.ReasonCode = "retrieval_budget_exhausted"
	}
	state.Sufficiency = decision
	state.SufficiencyChecked = true
	state.AnswerNeeded = true
	state.ToolCalls = nil
	state.ToolReason = ""
}

// emitContext 汇报当前模型上下文占用与证据数量，供前端在接近预算时告警。
// 只有用量或证据数发生变化时才发送，避免每轮重复事件。
func (h Harness) emitContext(state *RunState, emit Emit) error {
	usable := state.Budget.MaxTokens - state.Budget.ReserveTokens
	if usable < 1 {
		usable = state.Budget.MaxTokens
	}
	used := state.Budget.UsedTokens(state.Messages)
	evidence := 0
	if state.Pool != nil {
		evidence = state.Pool.Len()
	}
	if state.ContextReported && used == state.ContextTokens && evidence == state.ContextEvidence {
		return nil
	}
	state.ContextReported = true
	state.ContextTokens = used
	state.ContextEvidence = evidence
	if err := emit("context", map[string]any{
		"estimatedTokens": used,
		"usableTokens":    usable,
		"budgetTokens":    state.Budget.MaxTokens,
		"evidenceCount":   evidence,
		"evidenceLimit":   state.Limits.MaxEvidence,
	}); err != nil {
		state.MarkFailed(StopReasonCancelled)
		return err
	}
	return nil
}
