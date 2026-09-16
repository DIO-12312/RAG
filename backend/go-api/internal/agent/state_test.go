package agent

import (
	"errors"
	"testing"
)

func newRetrieveRun() *RunState {
	return NewRunState(DefaultRunLimits(), IntentResult{Action: "retrieve", StandaloneQuery: "q"}, []Message{{Role: "user", Content: "q"}})
}

func TestRunStateLegalPaths(t *testing.T) {
	cases := []struct {
		name string
		path []RunPhase
	}{
		{"retrieval answer", []RunPhase{RunPhaseModel, RunPhaseTool, RunPhaseAssess, RunPhaseFinalize, RunPhaseDone}},
		{"ordinary conversation", []RunPhase{RunPhaseModel, RunPhaseDone}},
		{"clarification", []RunPhase{RunPhaseDone}},
		{
			"insufficient evidence rewrite",
			[]RunPhase{RunPhaseModel, RunPhaseTool, RunPhaseAssess, RunPhaseRewrite, RunPhaseTool, RunPhaseAssess, RunPhaseFinalize, RunPhaseDone},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			state := newRetrieveRun()
			if state.Phase != RunPhaseRoute {
				t.Fatalf("initial phase = %s, want %s", state.Phase, RunPhaseRoute)
			}
			for _, phase := range tc.path {
				if err := state.TransitionTo(phase); err != nil {
					t.Fatalf("transition to %s failed: %v", phase, err)
				}
			}
			if state.Phase != RunPhaseDone {
				t.Fatalf("final phase = %s, want %s", state.Phase, RunPhaseDone)
			}
		})
	}
}

func TestRunStateRejectsIllegalTransitions(t *testing.T) {
	state := newRetrieveRun()
	if err := state.TransitionTo(RunPhaseTool); !errors.Is(err, ErrInvalidTransition) {
		t.Fatalf("Route -> Tool must be rejected, got %v", err)
	}

	done := newRetrieveRun()
	if err := done.TransitionTo(RunPhaseDone); err != nil {
		t.Fatalf("Route -> Done failed: %v", err)
	}
	if err := done.TransitionTo(RunPhaseTool); !errors.Is(err, ErrRunTerminal) {
		t.Fatalf("Done -> Tool must fail with terminal error, got %v", err)
	}
	if err := done.TransitionTo(RunPhaseModel); !errors.Is(err, ErrRunTerminal) {
		t.Fatalf("terminal run must not be reopened, got %v", err)
	}

}

func TestRunStateBudgetsAreEnforcedBeforeActions(t *testing.T) {
	state := newRetrieveRun()
	limits := state.Limits

	for range 20 {
		state.RecordModelCall()
	}
	if state.ModelCalls != 20 {
		t.Fatalf("model calls must remain observable without a hard limit: %d", state.ModelCalls)
	}

	for i := 0; i < limits.MaxRetrievalRounds; i++ {
		if err := state.CheckRetrievalRound(); err != nil {
			t.Fatalf("retrieval round %d rejected early: %v", i, err)
		}
		state.RecordRetrievalRound()
	}
	if err := state.CheckRetrievalRound(); !errors.Is(err, ErrBudgetExceeded) {
		t.Fatalf("retrieval round over budget accepted: %v", err)
	}

	for i := 0; i < limits.MaxRewriteRounds; i++ {
		if err := state.CheckRewriteRound(); err != nil {
			t.Fatalf("rewrite round %d rejected early: %v", i, err)
		}
		state.RecordRewriteRound()
	}
	if err := state.CheckRewriteRound(); !errors.Is(err, ErrBudgetExceeded) {
		t.Fatalf("rewrite round over budget accepted: %v", err)
	}

	if err := state.CheckToolCalls(limits.MaxToolCallsPerRound + 1); !errors.Is(err, ErrBudgetExceeded) {
		t.Fatalf("too many tool calls accepted: %v", err)
	}
	if err := state.CheckEvidence(limits.MaxEvidence + 1); !errors.Is(err, ErrBudgetExceeded) {
		t.Fatalf("evidence over budget accepted: %v", err)
	}
}

func TestRunStateStopReasonIsTerminal(t *testing.T) {
	for _, reason := range []StopReason{
		StopReasonCompleted,
		StopReasonDirectReply,
		StopReasonClarification,
		StopReasonEvidenceSufficient,
		StopReasonEvidenceInsufficient,
		StopReasonBudgetExceeded,
		StopReasonCancelled,
		StopReasonProviderError,
		StopReasonInvalidToolCall,
	} {
		state := newRetrieveRun()
		state.MarkStopped(reason)
		if !state.Stopped() || state.StopReason != reason {
			t.Fatalf("reason %s not recorded: %+v", reason, state)
		}
		if state.Phase != RunPhaseDone {
			t.Fatalf("terminal stop must move to Done, got %s", state.Phase)
		}
		if err := state.TransitionTo(RunPhaseModel); !errors.Is(err, ErrRunTerminal) {
			t.Fatalf("stopped run must not continue, got %v", err)
		}
	}

	failed := newRetrieveRun()
	failed.MarkFailed(StopReasonProviderError)
	if failed.Phase != RunPhaseFailed || !failed.Stopped() {
		t.Fatalf("failure must be terminal: %+v", failed)
	}
}

func TestDefaultRunLimitsMatchPlan(t *testing.T) {
	limits := DefaultRunLimits()
	if limits.MaxRetrievalRounds != 5 ||
		limits.MaxRewriteRounds != 2 ||
		limits.MaxToolCallsPerRound != 4 ||
		limits.MaxEvidence != 40 ||
		limits.MaxToolOutputBytes != 128*1024 {
		t.Fatalf("unexpected default limits: %+v", limits)
	}
}
