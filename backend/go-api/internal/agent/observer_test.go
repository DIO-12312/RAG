package agent

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"strings"
	"testing"
)

type recordingObserver struct {
	events []RunEvent
	panic  bool
}

func (o *recordingObserver) Observe(_ context.Context, event RunEvent) {
	if o.panic {
		panic("observer failure")
	}
	o.events = append(o.events, event)
}

func (o *recordingObserver) stages() []string {
	stages := make([]string, 0, len(o.events))
	for _, event := range o.events {
		stages = append(stages, event.Stage)
	}
	return stages
}

func observerHarness(observer Observer, model Model, tool Retriever, assessor SufficiencyAssessor, rewriter QueryRewriter) Harness {
	return Harness{
		Model:    model,
		Tool:     tool,
		Assessor: assessor,
		Rewriter: rewriter,
		Observer: observer,
		RunID:    "run-test-1",
	}
}

func TestRunEventsFollowStagesOnSuccess(t *testing.T) {
	observer := &recordingObserver{}
	model := &scriptedModel{responses: []Message{
		toolCallMessage("call-1", "migration"),
		{Content: "Migration ends in December. [1]"},
	}}
	assessor := &fakeAssessor{decision: SufficiencyDecision{Sufficient: true, ReasonCode: "covered"}}
	harness := observerHarness(observer, model, &retriever{}, assessor, nil)

	state, err := runScripted(t, harness, "question")
	if err != nil {
		t.Fatalf("run failed: %v", err)
	}

	want := []string{RunStageRoute, RunStageModel, RunStageTool, RunStageAssess, RunStageFinalize, RunStageComplete}
	got := observer.stages()
	if len(got) != len(want) {
		t.Fatalf("stages = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("stage %d = %q, want %q (all: %v)", i, got[i], want[i], got)
		}
	}
	terminal := observer.events[len(observer.events)-1]
	if terminal.RunID != "run-test-1" || terminal.StopReason != state.StopReason || terminal.ErrorCode != "" {
		t.Fatalf("terminal event must carry run id and stop reason: %+v", terminal)
	}
	if terminal.ModelCalls != state.ModelCalls || terminal.RetrievalCalls != state.RetrievalRounds {
		t.Fatalf("terminal event counters mismatch: %+v state=%+v", terminal, state)
	}
	toolEvent := observer.events[2]
	if toolEvent.QueryHash == "" || strings.Contains(toolEvent.QueryHash, "migration") {
		t.Fatalf("tool event must carry only a query fingerprint: %+v", toolEvent)
	}
}

func TestRunEventsIncludeRewriteStage(t *testing.T) {
	observer := &recordingObserver{}
	model := &scriptedModel{responses: []Message{
		toolCallMessage("call-1", "migration"),
		{Content: "答案 [1][2]"},
	}}
	tool := &scriptedRetriever{results: [][]Evidence{
		{{DocumentID: "d", IndexVersion: 1, ChunkID: "c1", Content: "first"}},
		{{DocumentID: "d", IndexVersion: 1, ChunkID: "c2", Content: "second"}},
	}}
	assessor := &fakeAssessor{queue: []SufficiencyDecision{
		{Sufficient: false, MissingFacts: []string{"结束时间"}, ReasonCode: "missing_fact"},
		{Sufficient: true, ReasonCode: "covered"},
	}}
	rewriter := &fakeRewriter{result: RewriteResult{Queries: []string{"migration 结束时间"}}}
	harness := observerHarness(observer, model, tool, assessor, rewriter)

	if _, err := runScripted(t, harness, "question"); err != nil {
		t.Fatalf("run failed: %v", err)
	}

	stages := observer.stages()
	joined := strings.Join(stages, ",")
	if !strings.Contains(joined, RunStageRewrite) {
		t.Fatalf("rewrite stage missing: %v", stages)
	}
	if stages[len(stages)-1] != RunStageComplete {
		t.Fatalf("last stage must be complete: %v", stages)
	}
	rewriteEvent := observer.events[indexOf(stages, RunStageRewrite)]
	if rewriteEvent.Round != 1 {
		t.Fatalf("rewrite round must be recorded: %+v", rewriteEvent)
	}
}

func TestRunEventsEmitSingleTerminalEventOnFailure(t *testing.T) {
	observer := &recordingObserver{}
	model := &scriptedModel{responses: []Message{{Content: "unused"}}}
	harness := observerHarness(observer, model, &retriever{}, nil, nil)

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	state := harness.newRunState("ds", "question", nil)
	err := harness.runStateMachine(ctx, state, func(string, any) error { return nil })
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("expected cancellation, got %v", err)
	}

	terminal := 0
	for _, event := range observer.events {
		if event.Stage == RunStageComplete {
			terminal++
			if event.ErrorCode == "" || event.StopReason != StopReasonCancelled {
				t.Fatalf("terminal event must record the failure: %+v", event)
			}
		}
	}
	if terminal != 1 {
		t.Fatalf("failure must emit exactly one terminal event, got %d (%v)", terminal, observer.stages())
	}
}

func TestRunEventsAreSanitized(t *testing.T) {
	observer := &recordingObserver{}
	secret := "sk-live-super-secret"
	model := &scriptedModel{responses: []Message{
		toolCallMessage("call-1", "migration"),
		{Content: "answer [1]", ReasoningContent: "private chain of thought"},
	}}
	assessor := &fakeAssessor{decision: SufficiencyDecision{Sufficient: true, ReasonCode: "covered"}}
	harness := observerHarness(observer, model, &retriever{}, assessor, nil)

	question := "请解释 " + secret + " 的用法"
	if _, err := runScripted(t, harness, question); err != nil {
		t.Fatalf("run failed: %v", err)
	}

	encoded, err := json.Marshal(observer.events)
	if err != nil {
		t.Fatalf("marshal events: %v", err)
	}
	payload := string(encoded)
	for _, forbidden := range []string{secret, question, "Migration ends in December", "private chain of thought", `{"query":"migration"}`} {
		if strings.Contains(payload, forbidden) {
			t.Fatalf("run events leaked %q: %s", forbidden, payload)
		}
	}
}

func TestObserverFailureDoesNotChangeRunResult(t *testing.T) {
	observer := &recordingObserver{panic: true}
	model := &scriptedModel{responses: []Message{{Content: "你好，有什么可以帮你？"}}}
	harness := observerHarness(observer, model, &retriever{}, nil, nil)

	state, err := runScripted(t, harness, "你好")
	if err != nil {
		t.Fatalf("observer panic must not fail the run: %v", err)
	}
	if state.Answer == "" || state.StopReason != StopReasonDirectReply {
		t.Fatalf("observer panic must not change the answer: %+v", state)
	}
	if len(observer.events) != 0 {
		t.Fatalf("panicking observer must not record events: %+v", observer.events)
	}
}

func TestQueryFingerprintIsShortAndStable(t *testing.T) {
	hash, length := QueryFingerprint("timeout 最大值")
	if len(hash) != 16 || length != len([]byte("timeout 最大值")) {
		t.Fatalf("unexpected fingerprint: %q %d", hash, length)
	}
	again, _ := QueryFingerprint("timeout 最大值")
	if again != hash {
		t.Fatal("fingerprint must be stable for identical input")
	}
	other, _ := QueryFingerprint("timeout 最小值")
	if other == hash {
		t.Fatal("different queries must not share a fingerprint")
	}
}

func indexOf(values []string, want string) int {
	for i, value := range values {
		if value == want {
			return i
		}
	}
	return -1
}

// TestJSONLogObserverEmitsParseableJSON 验证观测事件是单行 JSON 且字段可机读：
// SPEC 要求 slog 输出 JSON，文本 handler 无法按 stop_reason/error_code 聚合告警。
func TestJSONLogObserverEmitsParseableJSON(t *testing.T) {
	var buffer bytes.Buffer
	logger := slog.New(slog.NewJSONHandler(&buffer, &slog.HandlerOptions{Level: slog.LevelInfo}))
	JSONLogObserver{Logger: logger}.Observe(context.Background(), RunEvent{
		RunID:          "run-1",
		Stage:          RunStageComplete,
		Round:          2,
		Action:         "auto",
		QueryHash:      "abcdef0123456789",
		EvidenceCount:  6,
		ModelCalls:     4,
		RetrievalCalls: 1,
		RewriteCalls:   0,
		DurationMS:     123,
		StopReason:     "evidence_sufficient",
	})

	line := strings.TrimSpace(buffer.String())
	if strings.ContainsAny(line, "\n") {
		t.Fatalf("事件必须是单行: %q", line)
	}
	var payload map[string]any
	if err := json.Unmarshal([]byte(line), &payload); err != nil {
		t.Fatalf("事件不是合法 JSON: %v (%q)", err, line)
	}
	if payload["stage"] != "complete" || payload["stop_reason"] != "evidence_sufficient" {
		t.Fatalf("关键字段缺失或错误: %+v", payload)
	}
	if payload["run_id"] != "run-1" {
		t.Fatalf("run_id 缺失: %+v", payload)
	}
}
