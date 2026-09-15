package agent

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"
)

// scriptedModel 按调用顺序返回预设消息，并记录每次收到的工具策略。
type scriptedModel struct {
	responses []Message
	calls     int
	policies  []ToolPolicy
	messages  [][]Message
}

func (m *scriptedModel) Complete(ctx context.Context, messages []Message, policy ToolPolicy) (Message, error) {
	m.messages = append(m.messages, append([]Message(nil), messages...))
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
	t.Run("model calls have no count budget", func(t *testing.T) {
		model := &scriptedModel{responses: []Message{
			toolCallMessage("call-1", "q1"),
			toolCallMessage("call-2", "q2"),
			toolCallMessage("call-3", "q3"),
			{Content: "answer [1]"},
		}}
		tool := &retriever{}
		h := Harness{Model: model, Tool: tool}

		state, err := runScripted(t, h, "question")
		if err != nil {
			t.Fatalf("model call count must not stop the run: %v", err)
		}
		if state.StopReason != StopReasonCompleted || model.calls != 4 || state.ModelCalls != 4 {
			t.Fatalf("unexpected completed run: %+v model=%d", state, model.calls)
		}
	})

	t.Run("tool call overflow is truncated instead of failing", func(t *testing.T) {
		first := Message{}
		for i := 1; i <= 5; i++ {
			call := toolCallMessage(fmt.Sprintf("call-%d", i), fmt.Sprintf("q%d", i)).ToolCalls[0]
			first.ToolCalls = append(first.ToolCalls, call)
		}
		model := &scriptedModel{responses: []Message{first, {Content: "answer [1]"}}}
		tool := &retriever{}
		limits := DefaultRunLimits()
		limits.MaxToolCallsPerRound = 2
		h := Harness{Model: model, Tool: tool, Limits: limits}

		state, err := runScripted(t, h, "question")
		if err != nil {
			t.Fatalf("tool call overflow must converge instead of failing: %v", err)
		}
		if tool.calls != 2 || state.StopReason != StopReasonCompleted {
			t.Fatalf("unexpected bounded run: tool=%d state=%+v", tool.calls, state)
		}
		assertToolCallsPaired(t, state.Messages)
	})

	t.Run("evidence overflow keeps the bounded pool and completes", func(t *testing.T) {
		model := &scriptedModel{responses: []Message{
			toolCallMessage("call-1", "q1"),
			toolCallMessage("call-2", "q2"),
			{Content: "answer [1]"},
		}}
		tool := &scriptedRetriever{results: [][]Evidence{
			{{DocumentID: "d", IndexVersion: 1, ChunkID: "c1", Content: "first"}},
			{{DocumentID: "d", IndexVersion: 1, ChunkID: "c2", Content: "second"}},
		}}
		limits := DefaultRunLimits()
		limits.MaxEvidence = 1
		h := Harness{Model: model, Tool: tool, Limits: limits}

		state, err := runScripted(t, h, "question")
		if err != nil {
			t.Fatalf("evidence overflow must converge instead of failing: %v", err)
		}
		if tool.calls != 2 || state.Pool.Len() != 1 || state.StopReason != StopReasonCompleted {
			t.Fatalf("unexpected bounded run: tool=%d evidence=%d state=%+v", tool.calls, state.Pool.Len(), state)
		}
		assertToolCallsPaired(t, state.Messages)
	})

	t.Run("retrieval round budget converges instead of failing", func(t *testing.T) {
		model := &scriptedModel{responses: []Message{
			toolCallMessage("call-1", "q1"),
			toolCallMessage("call-2", "q2"),
			{Content: "现有证据不足，无法确认。"},
		}}
		tool := &retriever{}
		limits := DefaultRunLimits()
		limits.MaxRetrievalRounds = 1
		h := Harness{Model: model, Tool: tool, Limits: limits}

		state, err := runScripted(t, h, "question")
		if err != nil {
			t.Fatalf("exhausted retrieval budget must converge, not fail: %v", err)
		}
		if tool.calls != 1 || state.RetrievalRounds != 1 {
			t.Fatalf("retrieval must stop after one round: tool=%d rounds=%d", tool.calls, state.RetrievalRounds)
		}
		if state.StopReason != StopReasonEvidenceInsufficient || state.Phase != RunPhaseDone {
			t.Fatalf("unexpected convergence state: %+v", state)
		}
		assertToolCallsPaired(t, state.Messages)
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

// fakeAssessor 返回固定判断，并记录调用次数与输入。
type fakeAssessor struct {
	decision  SufficiencyDecision
	queue     []SufficiencyDecision
	err       error
	calls     int
	questions []string
	evidence  [][]Citation
}

func (a *fakeAssessor) Assess(ctx context.Context, question string, citations []Citation) (SufficiencyDecision, error) {
	a.calls++
	a.questions = append(a.questions, question)
	a.evidence = append(a.evidence, append([]Citation(nil), citations...))
	if a.err != nil {
		return SufficiencyDecision{}, a.err
	}
	if len(a.queue) > 0 {
		decision := a.queue[0]
		a.queue = a.queue[1:]
		return decision, ctx.Err()
	}
	return a.decision, ctx.Err()
}

// fakeRewriter 返回固定改写结果或错误，并记录收到的账本。
type fakeRewriter struct {
	result RewriteResult
	err    error
	calls  int
	last   RewriteRequest
}

func (r *fakeRewriter) Rewrite(ctx context.Context, request RewriteRequest) (RewriteResult, error) {
	r.calls++
	r.last = request
	if r.err != nil {
		return RewriteResult{}, r.err
	}
	return r.result, ctx.Err()
}

// scriptedRetriever 按调用顺序返回 Evidence 或错误。
type scriptedRetriever struct {
	results [][]Evidence
	errs    []error
	calls   int
	queries []string
}

func (r *scriptedRetriever) Retrieve(ctx context.Context, _ string, query string, _ int) ([]Evidence, error) {
	index := r.calls
	r.calls++
	r.queries = append(r.queries, query)
	if index < len(r.errs) && r.errs[index] != nil {
		return nil, r.errs[index]
	}
	if index < len(r.results) {
		return r.results[index], ctx.Err()
	}
	return nil, ctx.Err()
}

func lastSystemPrompt(messages []Message) string {
	for _, message := range messages {
		if message.Role == "system" {
			return message.Content
		}
	}
	return ""
}

func TestRuntimeSufficientEvidenceRetrievesOnceAndAnswers(t *testing.T) {
	model := &scriptedModel{responses: []Message{
		toolCallMessage("call-1", "migration"),
		{Content: "Migration ends in December. [1]"},
	}}
	tool := &retriever{}
	assessor := &fakeAssessor{decision: SufficiencyDecision{Sufficient: true, ReasonCode: "covered"}}
	h := Harness{Model: model, Tool: tool, Assessor: assessor}

	state, err := runScripted(t, h, "question")
	if err != nil {
		t.Fatalf("run failed: %v", err)
	}
	if tool.calls != 1 || assessor.calls != 1 || model.calls != 2 {
		t.Fatalf("unexpected call counts: tool=%d assessor=%d model=%d", tool.calls, assessor.calls, model.calls)
	}
	if state.StopReason != StopReasonEvidenceSufficient || len(state.Citations) != 1 {
		t.Fatalf("unexpected stop state: %+v", state)
	}
	if len(assessor.evidence[0]) != 1 || assessor.evidence[0][0].Evidence.Content == "" {
		t.Fatalf("assessor must receive retrieved evidence: %+v", assessor.evidence)
	}
}

func TestRuntimeInsufficientEvidenceConstrainsFinalAnswer(t *testing.T) {
	model := &scriptedModel{responses: []Message{
		toolCallMessage("call-1", "migration"),
		{Content: "现有资料只提到 migration，未给出结束时间。"},
	}}
	tool := &retriever{}
	assessor := &fakeAssessor{decision: SufficiencyDecision{
		Sufficient:   false,
		MissingFacts: []string{"migration 的结束时间"},
		ReasonCode:   "missing_fact",
	}}
	h := Harness{Model: model, Tool: tool, Assessor: assessor}

	state, err := runScripted(t, h, "question")
	if err != nil {
		t.Fatalf("run failed: %v", err)
	}
	if tool.calls != 1 || assessor.calls != 1 {
		t.Fatalf("insufficient evidence must not trigger extra retrieval here: tool=%d assessor=%d", tool.calls, assessor.calls)
	}
	if len(state.Citations) != 0 {
		t.Fatalf("answer without citations must not fabricate them: %+v", state.Citations)
	}
	if state.StopReason != StopReasonEvidenceInsufficient {
		t.Fatalf("unexpected stop reason: %+v", state)
	}
	prompt := lastSystemPrompt(model.messages[len(model.messages)-1])
	if !strings.Contains(prompt, "insufficient") {
		t.Fatalf("finalize must instruct the model about missing evidence: %s", prompt)
	}
}

func TestRuntimeAssessorUnavailableDegradesWithoutRetry(t *testing.T) {
	model := &scriptedModel{responses: []Message{
		toolCallMessage("call-1", "migration"),
		{Content: "无法确认。"},
	}}
	tool := &retriever{}
	assessor := &fakeAssessor{err: ErrSufficiencyUnavailable}
	h := Harness{Model: model, Tool: tool, Assessor: assessor}

	state, err := runScripted(t, h, "question")
	if err != nil {
		t.Fatalf("degraded assessor must not fail the run: %v", err)
	}
	if assessor.calls != 1 {
		t.Fatalf("assessor failure must not retry: %d", assessor.calls)
	}
	if tool.calls != 1 {
		t.Fatalf("degraded assessor must not start extra retrieval: %d", tool.calls)
	}
	if state.StopReason != StopReasonEvidenceInsufficient {
		t.Fatalf("unexpected stop reason: %+v", state)
	}
	if model.calls != 2 {
		t.Fatalf("expected tool decision + constrained answer, got %d calls", model.calls)
	}
}

func TestRuntimeAssessorCancellationTerminatesImmediately(t *testing.T) {
	model := &scriptedModel{responses: []Message{
		toolCallMessage("call-1", "migration"),
		{Content: "unused"},
	}}
	tool := &retriever{}
	assessor := &fakeAssessor{err: context.Canceled}
	h := Harness{Model: model, Tool: tool, Assessor: assessor}

	state, err := runScripted(t, h, "question")
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("cancellation must abort the run, got %v", err)
	}
	if state.StopReason != StopReasonCancelled || model.calls != 1 {
		t.Fatalf("cancelled assessor must stop the loop: %+v model=%d", state, model.calls)
	}
}

func TestRuntimeOrdinaryConversationNeverAssesses(t *testing.T) {
	model := &scriptedModel{responses: []Message{{Content: "你好，有什么可以帮你？"}}}
	tool := &retriever{}
	assessor := &fakeAssessor{decision: SufficiencyDecision{Sufficient: true}}
	h := Harness{Model: model, Tool: tool, Assessor: assessor}

	state, err := runScripted(t, h, "你好")
	if err != nil {
		t.Fatalf("run failed: %v", err)
	}
	if assessor.calls != 0 || tool.calls != 0 {
		t.Fatalf("ordinary conversation must not assess or retrieve: assessor=%d tool=%d", assessor.calls, tool.calls)
	}
	if state.StopReason != StopReasonDirectReply {
		t.Fatalf("unexpected stop reason: %+v", state)
	}
}

func TestRuntimeAssessorCallsAreObservedWithoutCountLimit(t *testing.T) {
	model := &scriptedModel{responses: []Message{
		toolCallMessage("call-1", "migration"),
		{Content: "unused answer"},
	}}
	tool := &retriever{}
	assessor := &fakeAssessor{decision: SufficiencyDecision{Sufficient: true, ReasonCode: "covered"}}
	h := Harness{Model: model, Tool: tool, Assessor: assessor}

	state, err := runScripted(t, h, "question")
	if err != nil {
		t.Fatalf("assessor and final answer must not be blocked by a call count: %v", err)
	}
	// 工具决策、Assess 与 Finalize 都保留在 ModelCalls 中，供策略和观测使用。
	if state.StopReason != StopReasonEvidenceSufficient || state.ModelCalls != 3 || model.calls != 2 {
		t.Fatalf("unexpected stop state: %+v model=%d", state, model.calls)
	}
}

func TestRuntimeInsufficientThenSufficientClosesLoop(t *testing.T) {
	first := []Evidence{{DocumentID: "d1", IndexVersion: 1, ChunkID: "c1", Content: "first round evidence"}}
	second := []Evidence{
		{DocumentID: "d1", IndexVersion: 1, ChunkID: "c1", Content: "first round evidence"},
		{DocumentID: "d2", IndexVersion: 1, ChunkID: "c2", Content: "second round evidence"},
	}
	model := &scriptedModel{responses: []Message{
		toolCallMessage("call-1", "migration"),
		{Content: "答案 [1][2]"},
	}}
	tool := &scriptedRetriever{results: [][]Evidence{first, second}}
	assessor := &fakeAssessor{queue: []SufficiencyDecision{
		{Sufficient: false, MissingFacts: []string{"结束时间"}, ReasonCode: "missing_fact"},
		{Sufficient: true, ReasonCode: "covered"},
	}}
	rewriter := &fakeRewriter{result: RewriteResult{Queries: []string{"migration 结束时间"}}}
	h := Harness{Model: model, Tool: tool, Assessor: assessor, Rewriter: rewriter}

	state, err := runScripted(t, h, "question")
	if err != nil {
		t.Fatalf("loop failed: %v", err)
	}
	if tool.calls != 2 || assessor.calls != 2 || rewriter.calls != 1 || model.calls != 2 {
		t.Fatalf("unexpected call counts: tool=%d assessor=%d rewriter=%d model=%d",
			tool.calls, assessor.calls, rewriter.calls, model.calls)
	}
	if state.StopReason != StopReasonEvidenceSufficient || len(state.Citations) != 2 {
		t.Fatalf("unexpected stop state: %+v", state)
	}
	if len(state.AttemptedQueries) != 2 || state.AttemptedQueries[1] != "migration 结束时间" {
		t.Fatalf("attempt ledger must record both queries: %+v", state.AttemptedQueries)
	}
	if len(rewriter.last.MissingFacts) != 1 || rewriter.last.MissingFacts[0] != "结束时间" {
		t.Fatalf("rewriter must receive missing facts: %+v", rewriter.last)
	}
	if len(rewriter.last.AttemptedQueries) != 1 || rewriter.last.AttemptedQueries[0] != "question" {
		t.Fatalf("rewriter must receive the attempt ledger: %+v", rewriter.last)
	}
}

func TestRuntimeRewriteLoopConvergesWithoutNewQuery(t *testing.T) {
	model := &scriptedModel{responses: []Message{
		toolCallMessage("call-1", "migration"),
		{Content: "无法确认结束时间。"},
	}}
	tool := &scriptedRetriever{results: [][]Evidence{{{DocumentID: "d", IndexVersion: 1, ChunkID: "c", Content: "partial"}}}}
	assessor := &fakeAssessor{decision: SufficiencyDecision{Sufficient: false, MissingFacts: []string{"结束时间"}, ReasonCode: "missing_fact"}}
	rewriter := &fakeRewriter{err: ErrNoNewQuery}
	h := Harness{Model: model, Tool: tool, Assessor: assessor, Rewriter: rewriter}

	state, err := runScripted(t, h, "question")
	if err != nil {
		t.Fatalf("convergence must not fail: %v", err)
	}
	if rewriter.calls != 1 || tool.calls != 1 || assessor.calls != 1 {
		t.Fatalf("no_new_query must converge immediately: rewriter=%d tool=%d assessor=%d", rewriter.calls, tool.calls, assessor.calls)
	}
	if state.StopReason != StopReasonEvidenceInsufficient {
		t.Fatalf("unexpected stop reason: %+v", state)
	}
	if !strings.Contains(lastSystemPrompt(model.messages[len(model.messages)-1]), "insufficient") {
		t.Fatal("converged answer must carry the insufficiency constraint")
	}
}

func TestRuntimeRewriteBudgetBoundsTheLoop(t *testing.T) {
	model := &scriptedModel{responses: []Message{
		toolCallMessage("call-1", "migration"),
		{Content: "仍无法确认。"},
	}}
	tool := &scriptedRetriever{results: [][]Evidence{
		{{DocumentID: "d", IndexVersion: 1, ChunkID: "c1", Content: "round one"}},
		{{DocumentID: "d", IndexVersion: 1, ChunkID: "c2", Content: "round two"}},
	}}
	assessor := &fakeAssessor{decision: SufficiencyDecision{Sufficient: false, MissingFacts: []string{"结束时间"}, ReasonCode: "missing_fact"}}
	rewriter := &fakeRewriter{result: RewriteResult{Queries: []string{"migration 结束时间"}}}
	limits := DefaultRunLimits()
	limits.MaxRewriteRounds = 1
	h := Harness{Model: model, Tool: tool, Assessor: assessor, Rewriter: rewriter, Limits: limits}

	state, err := runScripted(t, h, "question")
	if err != nil {
		t.Fatalf("bounded loop must not fail: %v", err)
	}
	if rewriter.calls != 1 || tool.calls != 2 || assessor.calls != 2 {
		t.Fatalf("rewrite budget must stop the loop: rewriter=%d tool=%d assessor=%d", rewriter.calls, tool.calls, assessor.calls)
	}
	if state.StopReason != StopReasonEvidenceInsufficient {
		t.Fatalf("unexpected stop reason: %+v", state)
	}
}

func TestRuntimeDuplicateQuerySkipsRetrievalWithoutConsumingRound(t *testing.T) {
	model := &scriptedModel{responses: []Message{
		toolCallMessage("call-1", "same query"),
		toolCallMessage("call-2", "same query"),
		{Content: "答案 [1]"},
	}}
	tool := &scriptedRetriever{results: [][]Evidence{{{DocumentID: "d", IndexVersion: 1, ChunkID: "c", Content: "evidence"}}}}
	h := Harness{Model: model, Tool: tool}

	state, err := runScripted(t, h, "same query")
	if err != nil {
		t.Fatalf("run failed: %v", err)
	}
	if tool.calls != 1 || state.RetrievalRounds != 1 {
		t.Fatalf("duplicate query must not retrieve or consume a round: tool=%d rounds=%d", tool.calls, state.RetrievalRounds)
	}
	if len(state.Citations) != 1 || state.Citations[0].Ordinal != 1 {
		t.Fatalf("citation ordinals must stay stable: %+v", state.Citations)
	}
}

func TestRuntimeCancellationDuringSecondRetrievalStopsLoop(t *testing.T) {
	model := &scriptedModel{responses: []Message{
		toolCallMessage("call-1", "migration"),
		{Content: "unused answer"},
	}}
	tool := &scriptedRetriever{
		results: [][]Evidence{{{DocumentID: "d", IndexVersion: 1, ChunkID: "c1", Content: "first"}}},
		errs:    []error{nil, context.Canceled},
	}
	assessor := &fakeAssessor{decision: SufficiencyDecision{Sufficient: false, MissingFacts: []string{"结束时间"}, ReasonCode: "missing_fact"}}
	rewriter := &fakeRewriter{result: RewriteResult{Queries: []string{"migration 结束时间"}}}
	h := Harness{Model: model, Tool: tool, Assessor: assessor, Rewriter: rewriter}

	state, err := runScripted(t, h, "question")
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("expected cancellation, got %v", err)
	}
	if state.StopReason != StopReasonCancelled || model.calls != 1 || tool.calls != 2 {
		t.Fatalf("unexpected stop state: %+v model=%d tool=%d", state, model.calls, tool.calls)
	}
}

// assertToolCallsPaired 校验每个 assistant tool_call 都有配对的 tool 结果，
// 收敛路径也不能留下悬空调用。
func assertToolCallsPaired(t *testing.T, messages []Message) {
	t.Helper()
	answered := map[string]bool{}
	for _, message := range messages {
		if message.Role == "tool" && message.ToolCallID != "" {
			answered[message.ToolCallID] = true
		}
	}
	for _, message := range messages {
		for _, call := range message.ToolCalls {
			if call.ID != "" && !answered[call.ID] {
				t.Fatalf("tool call %s has no paired result: %+v", call.ID, messages)
			}
		}
	}
}

func TestRuntimeRetrievalBudgetConvergesToInsufficientAnswer(t *testing.T) {
	model := &scriptedModel{responses: []Message{
		toolCallMessage("call-1", "first"),
		{Content: "现有证据不足，无法确认结束时间。"},
	}}
	tool := &scriptedRetriever{results: [][]Evidence{{{DocumentID: "d", IndexVersion: 1, ChunkID: "c1", Content: "first"}}}}
	assessor := &fakeAssessor{decision: SufficiencyDecision{
		Sufficient:   false,
		MissingFacts: []string{"结束时间"},
		ReasonCode:   "missing_fact",
	}}
	rewriter := &fakeRewriter{result: RewriteResult{Queries: []string{"second", "third"}}}
	limits := DefaultRunLimits()
	limits.MaxRetrievalRounds = 1
	h := Harness{Model: model, Tool: tool, Assessor: assessor, Rewriter: rewriter, Limits: limits}

	state, err := runScripted(t, h, "question")
	if err != nil {
		t.Fatalf("exhausted retrieval budget must converge, not fail: %v", err)
	}
	if tool.calls != 1 || state.RetrievalRounds != 1 {
		t.Fatalf("must not exceed the retrieval budget: tool=%d rounds=%d", tool.calls, state.RetrievalRounds)
	}
	if state.StopReason != StopReasonEvidenceInsufficient || state.Phase != RunPhaseDone {
		t.Fatalf("unexpected stop state: %+v", state)
	}
	if !strings.Contains(lastSystemPrompt(model.messages[len(model.messages)-1]), "insufficient") {
		t.Fatal("converged answer must carry the insufficiency constraint")
	}
	if len(state.Sufficiency.MissingFacts) != 1 || state.Sufficiency.MissingFacts[0] != "结束时间" {
		t.Fatalf("existing gaps must be preserved: %+v", state.Sufficiency)
	}
	assertToolCallsPaired(t, state.Messages)
}

func TestFailureHintMapsStopReasons(t *testing.T) {
	cases := []struct {
		reason StopReason
		want   string
	}{
		{StopReasonBudgetExceeded, "RUN_BUDGET_EXCEEDED"},
		{StopReasonInvalidToolCall, "TOOL_CALL_INVALID"},
		{StopReasonProviderError, "MODEL_UNAVAILABLE"},
		{StopReasonCancelled, "REQUEST_CANCELLED"},
	}
	for _, tc := range cases {
		code, message := FailureHint(&RunError{Reason: tc.reason, Err: errors.New("boom")})
		if code != tc.want || message == "" {
			t.Fatalf("reason %s mapped to (%s, %q)", tc.reason, code, message)
		}
	}

	code, message := FailureHint(errors.New("plain failure"))
	if code != "CHAT_FAILED" || message == "" {
		t.Fatalf("unknown error must keep the generic hint, got (%s, %q)", code, message)
	}

	wrapped := &RunError{Reason: StopReasonCancelled, Err: context.Canceled}
	if !errors.Is(wrapped, context.Canceled) {
		t.Fatal("RunError must unwrap to the original error")
	}
}

// TestRuntimeReportsContextUsage 验证每轮模型调用前汇报上下文占用，
// 且证据进入上下文后用量必须增长。
func TestRuntimeReportsContextUsage(t *testing.T) {
	model := &scriptedModel{responses: []Message{
		toolCallMessage("call-1", "migration"),
		{Content: "Migration ends in December. [1]"},
	}}
	h := Harness{Model: model, Tool: &retriever{}}
	state := h.newRunState("owned-dataset", "question", nil)

	type report struct {
		tokens   int
		usable   int
		evidence int
		limit    int
	}
	var reports []report
	err := h.runStateMachine(context.Background(), state, func(event string, data any) error {
		if event != "context" {
			return nil
		}
		payload, ok := data.(map[string]any)
		if !ok {
			t.Fatalf("unexpected context payload: %T", data)
		}
		item := report{}
		item.tokens, _ = payload["estimatedTokens"].(int)
		item.usable, _ = payload["usableTokens"].(int)
		item.evidence, _ = payload["evidenceCount"].(int)
		item.limit, _ = payload["evidenceLimit"].(int)
		reports = append(reports, item)
		return nil
	})
	if err != nil {
		t.Fatalf("run failed: %v", err)
	}
	if len(reports) != 2 {
		t.Fatalf("expected one context report per model round, got %d", len(reports))
	}
	if reports[0].evidence != 0 || reports[1].evidence != 1 {
		t.Fatalf("unexpected evidence counts: %+v", reports)
	}
	if reports[1].tokens <= reports[0].tokens {
		t.Fatalf("evidence must increase context usage: %+v", reports)
	}
	budget := DefaultContextBudget()
	if reports[1].usable != budget.MaxTokens-budget.ReserveTokens || reports[1].limit != DefaultRunLimits().MaxEvidence {
		t.Fatalf("unexpected budget limits: %+v", reports[1])
	}
}

// TestEmitContextSkipsUnchangedUsage 验证用量没有变化时不重复发送占用事件。
func TestEmitContextSkipsUnchangedUsage(t *testing.T) {
	h := Harness{}
	state := h.newRunState("dataset", "question", nil)
	count := 0
	emit := func(string, any) error { count++; return nil }
	if err := h.emitContext(state, emit); err != nil {
		t.Fatalf("first report failed: %v", err)
	}
	if err := h.emitContext(state, emit); err != nil {
		t.Fatalf("second report failed: %v", err)
	}
	if count != 1 {
		t.Fatalf("unchanged usage must not repeat the event, got %d", count)
	}
}
