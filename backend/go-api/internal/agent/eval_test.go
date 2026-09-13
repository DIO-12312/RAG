package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

// EvalMetrics 是固定评测集的五项硬门槛指标。
type EvalMetrics struct {
	RouteAccuracy           float64
	RequiredRetrievalRecall float64
	NoRetrievalPrecision    float64
	LoopConvergence         float64
	CitationValidity        float64
}

type evalCase struct {
	ID                 string    `json:"id"`
	Question           string    `json:"question"`
	History            []Message `json:"history"`
	ExpectedAction     string    `json:"expected_action"`
	RequiredQueries    []string  `json:"required_queries"`
	MaxRetrievalRounds int       `json:"max_retrieval_rounds"`
	ExpectedStopReason string    `json:"expected_stop_reason"`
}

func loadEvalCases(t *testing.T) []evalCase {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("testdata", "agent_eval.json"))
	if err != nil {
		t.Fatalf("read eval fixture: %v", err)
	}
	var cases []evalCase
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatalf("decode eval fixture: %v", err)
	}
	if len(cases) < 30 {
		t.Fatalf("eval fixture must cover at least 30 samples, got %d", len(cases))
	}
	return cases
}

// evalTracker 保存单个样本的尝试账本与调用次数，驱动确定性的 Oracle 协作者。
type evalTracker struct {
	spec          evalCase
	attempted     []string
	retrieverCall int
	answerCalls   int
	assessCalls   int
	rewriteCalls  int
}

func (t *evalTracker) pending() []string {
	var pending []string
	for _, required := range t.spec.RequiredQueries {
		if !t.attemptedContains(required) {
			pending = append(pending, required)
		}
	}
	return pending
}

func (t *evalTracker) attemptedContains(query string) bool {
	normalized := NormalizeAttemptedQuery(query)
	for _, attempted := range t.attempted {
		if NormalizeAttemptedQuery(attempted) == normalized {
			return true
		}
	}
	return false
}

// answerText 由期望终止原因推导：充分时带引用，不足时明确说明证据不足。
func (t *evalTracker) answerText() string {
	if t.spec.ExpectedStopReason == string(StopReasonEvidenceSufficient) {
		return "评测回答 [1]"
	}
	return "现有证据不足，无法确认该事实。"
}

type evalModel struct{ tracker *evalTracker }

func (m evalModel) Complete(ctx context.Context, _ []Message, policy ToolPolicy) (Message, error) {
	if policy.Mode == ToolNone {
		m.tracker.answerCalls++
		return Message{Content: m.tracker.answerText()}, ctx.Err()
	}
	pending := m.tracker.pending()
	query := m.tracker.spec.Question
	if len(pending) > 0 {
		query = pending[0]
	}
	return toolCallMessage(fmt.Sprintf("eval-call-%d", len(m.tracker.attempted)+1), query), ctx.Err()
}

type evalAssessor struct{ tracker *evalTracker }

func (a evalAssessor) Assess(ctx context.Context, _ string, citations []Citation) (SufficiencyDecision, error) {
	a.tracker.assessCalls++
	pending := a.tracker.pending()
	if len(pending) == 0 && len(citations) > 0 {
		return SufficiencyDecision{Sufficient: true, ReasonCode: "covered"}, ctx.Err()
	}
	gaps := pending
	if len(gaps) == 0 {
		gaps = []string{"缺少可支撑事实的证据"}
	}
	return SufficiencyDecision{Sufficient: false, MissingFacts: gaps, ReasonCode: "missing_facet"}, ctx.Err()
}

type evalRewriter struct{ tracker *evalTracker }

func (r evalRewriter) Rewrite(ctx context.Context, _ RewriteRequest) (RewriteResult, error) {
	r.tracker.rewriteCalls++
	pending := r.tracker.pending()
	if len(pending) == 0 {
		return RewriteResult{}, ErrNoNewQuery
	}
	if len(pending) > maxRewriteQueries {
		pending = pending[:maxRewriteQueries]
	}
	return RewriteResult{Queries: append([]string(nil), pending...)}, ctx.Err()
}

type evalRetriever struct{ tracker *evalTracker }

func (r evalRetriever) Retrieve(ctx context.Context, _ string, query string, _ int) ([]Evidence, error) {
	r.tracker.retrieverCall++
	r.tracker.attempted = append(r.tracker.attempted, query)
	if len(r.tracker.spec.RequiredQueries) == 0 {
		// 无答案样本：可检索但没有可支撑事实的 Evidence。
		return nil, ctx.Err()
	}
	return []Evidence{{
		DocumentID:   "eval-doc",
		IndexVersion: 1,
		ChunkID:      fmt.Sprintf("chunk-%d", r.tracker.retrieverCall),
		Content:      "eval evidence for " + query,
		SourceName:   "eval.md",
		Locator:      "L1",
	}}, ctx.Err()
}

// TestAgentEvalFixture 用脚本化协作者跑固定样本，门槛全部为 100%，
// 不 snapshot 任何 LLM 自由文本。
func TestAgentEvalFixture(t *testing.T) {
	cases := loadEvalCases(t)

	var routeHits, routeTotal int
	var requiredHits, requiredTotal int
	var noRetrievalHits, noRetrievalTotal int
	var converged, citationValid int

	for _, spec := range cases {
		t.Run(spec.ID, func(t *testing.T) {
			intent := RouteIntent(spec.Question, spec.History)
			routeTotal++
			if intent.Action == spec.ExpectedAction {
				routeHits++
			} else {
				t.Errorf("route action = %q, want %q", intent.Action, spec.ExpectedAction)
			}

			tracker := &evalTracker{spec: spec}
			harness := Harness{
				Model:    evalModel{tracker: tracker},
				Tool:     evalRetriever{tracker: tracker},
				Assessor: evalAssessor{tracker: tracker},
				Rewriter: evalRewriter{tracker: tracker},
			}
			state := harness.newRunState("eval-dataset", spec.Question, spec.History)
			if err := harness.runStateMachine(context.Background(), state, func(string, any) error { return nil }); err != nil {
				t.Fatalf("run failed: %v", err)
			}

			if state.Stopped() {
				converged++
			} else {
				t.Error("run did not converge")
			}
			if string(state.StopReason) != spec.ExpectedStopReason {
				t.Errorf("stop reason = %q, want %q", state.StopReason, spec.ExpectedStopReason)
			}

			if spec.ExpectedAction == "retrieve" {
				for _, required := range spec.RequiredQueries {
					requiredTotal++
					if tracker.attemptedContains(required) {
						requiredHits++
					} else {
						t.Errorf("required query never retrieved: %q (attempted %v)", required, tracker.attempted)
					}
				}
				if spec.MaxRetrievalRounds >= 0 && state.RetrievalRounds > spec.MaxRetrievalRounds {
					t.Errorf("retrieval rounds = %d, want <= %d", state.RetrievalRounds, spec.MaxRetrievalRounds)
				}
			} else {
				noRetrievalTotal++
				if tracker.retrieverCall == 0 && tracker.assessCalls == 0 && tracker.rewriteCalls == 0 {
					noRetrievalHits++
				} else {
					t.Errorf("direct path must not retrieve/assess/rewrite: tool=%d assess=%d rewrite=%d",
						tracker.retrieverCall, tracker.assessCalls, tracker.rewriteCalls)
				}
			}

			pool := state.Pool.Citations()
			seen := map[int]bool{}
			valid := true
			for _, citation := range state.Citations {
				if citation.Ordinal < 1 || citation.Ordinal > len(pool) || seen[citation.Ordinal] {
					valid = false
				}
				seen[citation.Ordinal] = true
			}
			if valid {
				citationValid++
			} else {
				t.Errorf("invalid citation ordinals: %+v (pool=%d)", state.Citations, len(pool))
			}
		})
	}

	metrics := EvalMetrics{
		RouteAccuracy:           ratio(routeHits, routeTotal),
		RequiredRetrievalRecall: ratio(requiredHits, requiredTotal),
		NoRetrievalPrecision:    ratio(noRetrievalHits, noRetrievalTotal),
		LoopConvergence:         ratio(converged, len(cases)),
		CitationValidity:        ratio(citationValid, len(cases)),
	}
	t.Logf("eval metrics: %+v", metrics)

	if metrics.RouteAccuracy != 1 ||
		metrics.RequiredRetrievalRecall != 1 ||
		metrics.NoRetrievalPrecision != 1 ||
		metrics.LoopConvergence != 1 ||
		metrics.CitationValidity != 1 {
		t.Fatalf("eval thresholds must stay at 100%%: %+v", metrics)
	}
}

// TestAgentEvalCancellationCoverage 覆盖计划要求的取消类别：
// 取消必须立即终止，不再调用模型或工具。
func TestAgentEvalCancellationCoverage(t *testing.T) {
	spec := evalCase{
		ID:                 "cancellation",
		Question:           "timeout 最大是多少？",
		ExpectedAction:     "retrieve",
		RequiredQueries:    []string{"timeout 最大是多少？"},
		MaxRetrievalRounds: 1,
		ExpectedStopReason: string(StopReasonCancelled),
	}
	tracker := &evalTracker{spec: spec}
	harness := Harness{
		Model:    evalModel{tracker: tracker},
		Tool:     evalRetriever{tracker: tracker},
		Assessor: evalAssessor{tracker: tracker},
		Rewriter: evalRewriter{tracker: tracker},
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	state := harness.newRunState("eval-dataset", spec.Question, spec.History)
	err := harness.runStateMachine(ctx, state, func(string, any) error { return nil })
	if err == nil {
		t.Fatal("cancelled run must return an error")
	}
	if state.StopReason != StopReasonCancelled || !state.Stopped() {
		t.Fatalf("cancelled run must stop: %+v", state)
	}
	if tracker.retrieverCall != 0 || tracker.answerCalls != 0 {
		t.Fatalf("cancelled run must not call downstream: tool=%d model=%d", tracker.retrieverCall, tracker.answerCalls)
	}
}

func ratio(part, total int) float64 {
	if total == 0 {
		return 1
	}
	return float64(part) / float64(total)
}
