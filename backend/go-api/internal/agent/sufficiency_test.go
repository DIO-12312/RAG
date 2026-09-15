package agent

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"
)

// assessorModel 记录每次请求的消息与工具策略，并按顺序返回预设结果。
type assessorModel struct {
	responses []Message
	errs      []error
	messages  [][]Message
	policies  []ToolPolicy
	calls     int
}

func (m *assessorModel) Complete(ctx context.Context, messages []Message, policy ToolPolicy) (Message, error) {
	m.messages = append(m.messages, append([]Message(nil), messages...))
	m.policies = append(m.policies, policy)
	index := m.calls
	m.calls++
	if index < len(m.errs) && m.errs[index] != nil {
		return Message{}, m.errs[index]
	}
	if index < len(m.responses) {
		return m.responses[index], ctx.Err()
	}
	return Message{}, fmt.Errorf("unexpected assessor call %d", m.calls)
}

func TestSufficiencyStrictJSONContract(t *testing.T) {
	valid := `{"sufficient":false,"missing_facts":["timeout 的最大允许值"],"reason_code":"missing_parameter_limit"}`
	decision, err := parseSufficiencyDecision(valid)
	if err != nil {
		t.Fatalf("valid decision rejected: %v", err)
	}
	if decision.Sufficient || len(decision.MissingFacts) != 1 || decision.ReasonCode != "missing_parameter_limit" {
		t.Fatalf("unexpected decision: %+v", decision)
	}

	sufficient := `{"sufficient":true,"missing_facts":[],"reason_code":"covered"}`
	if parsed, err := parseSufficiencyDecision(sufficient); err != nil || !parsed.Sufficient || len(parsed.MissingFacts) != 0 {
		t.Fatalf("sufficient decision rejected: %+v %v", parsed, err)
	}

	tooManyGaps := `{"sufficient":false,"missing_facts":["a","b","c","d","e","f"],"reason_code":"x"}`
	longGap := fmt.Sprintf(`{"sufficient":false,"missing_facts":[%q],"reason_code":"x"}`, strings.Repeat("x", 257))

	invalid := []struct {
		name string
		body string
	}{
		{"markdown fence", "```json\n" + valid + "\n```"},
		{"missing sufficient", `{"missing_facts":[],"reason_code":"x"}`},
		{"missing missing_facts", `{"sufficient":true,"reason_code":"x"}`},
		{"missing reason_code", `{"sufficient":true,"missing_facts":[]}`},
		{"too many gaps", tooManyGaps},
		{"gap too long", longGap},
		{"unknown extra field", `{"sufficient":true,"missing_facts":[],"reason_code":"x","reasoning":"because"}`},
		{"trailing prose", valid + " 以上是判断结果"},
		{"empty", ""},
		{"blank reason code", `{"sufficient":true,"missing_facts":[],"reason_code":"  "}`},
	}

	for _, tc := range invalid {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := parseSufficiencyDecision(tc.body); !errors.Is(err, ErrSufficiencyUnavailable) {
				t.Fatalf("invalid decision accepted: %v", err)
			}
		})
	}
}

func TestModelSufficiencyAssessorDecisionMapping(t *testing.T) {
	cases := []struct {
		name           string
		question       string
		citations      []Citation
		response       string
		wantSufficient bool
		wantMissing    []string
		wantEvidence   string
	}{
		{
			name:           "direct evidence sufficient",
			question:       "timeout 最大是多少？",
			citations:      []Citation{{Ordinal: 1, Evidence: Evidence{SourceName: "guide.md", Locator: "L42", Content: "timeout 最大 3600 秒"}}},
			response:       `{"sufficient":true,"missing_facts":[],"reason_code":"covered"}`,
			wantSufficient: true,
			wantEvidence:   "timeout 最大 3600 秒",
		},
		{
			name:           "cross document comparison missing one side",
			question:       "A 与 B 的默认值差多少？",
			citations:      []Citation{{Ordinal: 1, Evidence: Evidence{SourceName: "a.md", Locator: "L1", Content: "A 默认 10"}}},
			response:       `{"sufficient":false,"missing_facts":["B 的默认值"],"reason_code":"missing_document"}`,
			wantSufficient: false,
			wantMissing:    []string{"B 的默认值"},
			wantEvidence:   "A 默认 10",
		},
		{
			name:           "empty recall",
			question:       "timeout 最大是多少？",
			citations:      nil,
			response:       `{"sufficient":false,"missing_facts":["timeout 的最大值"],"reason_code":"no_evidence"}`,
			wantSufficient: false,
			wantMissing:    []string{"timeout 的最大值"},
			wantEvidence:   "no evidence retrieved",
		},
		{
			name:           "keyword only evidence",
			question:       "timeout 最大是多少？",
			citations:      []Citation{{Ordinal: 1, Evidence: Evidence{SourceName: "b.md", Locator: "L9", Content: "本文提到 timeout 一词但未给出数值"}}},
			response:       `{"sufficient":false,"missing_facts":["timeout 的最大允许值"],"reason_code":"insufficient_detail"}`,
			wantSufficient: false,
			wantMissing:    []string{"timeout 的最大允许值"},
			wantEvidence:   "未给出数值",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			model := &assessorModel{responses: []Message{{Content: tc.response}}}
			assessor := ModelSufficiencyAssessor{Model: model}

			decision, err := assessor.Assess(context.Background(), tc.question, tc.citations)
			if err != nil {
				t.Fatalf("assess failed: %v", err)
			}
			if decision.Sufficient != tc.wantSufficient {
				t.Fatalf("sufficient = %v, want %v", decision.Sufficient, tc.wantSufficient)
			}
			if len(decision.MissingFacts) != len(tc.wantMissing) {
				t.Fatalf("missing facts = %v, want %v", decision.MissingFacts, tc.wantMissing)
			}
			for i, want := range tc.wantMissing {
				if decision.MissingFacts[i] != want {
					t.Fatalf("missing fact %d = %q, want %q", i, decision.MissingFacts[i], want)
				}
			}
			if model.calls != 1 {
				t.Fatalf("expected one assessor model call, got %d", model.calls)
			}
			if model.policies[0].Temperature == nil || *model.policies[0].Temperature != 0 {
				t.Fatalf("assessor must request deterministic sampling, got %+v", model.policies[0])
			}
			if model.policies[0].Mode != ToolNone {
				t.Fatalf("assessor must use ToolNone, got %+v", model.policies[0])
			}
			request := model.messages[0][len(model.messages[0])-1].Content
			if !strings.Contains(request, tc.question) || !strings.Contains(request, tc.wantEvidence) {
				t.Fatalf("assessor request missing question/evidence:\n%s", request)
			}
		})
	}
}

func TestModelSufficiencyAssessorErrorsAreTyped(t *testing.T) {
	citations := []Citation{{Ordinal: 1, Evidence: Evidence{Content: "evidence"}}}

	t.Run("invalid json", func(t *testing.T) {
		model := &assessorModel{responses: []Message{{Content: "not json"}}}
		assessor := ModelSufficiencyAssessor{Model: model}
		if _, err := assessor.Assess(context.Background(), "q", citations); !errors.Is(err, ErrSufficiencyUnavailable) {
			t.Fatalf("invalid json must be typed unavailable: %v", err)
		}
	})

	t.Run("provider failure", func(t *testing.T) {
		model := &assessorModel{errs: []error{errors.New("model returned HTTP 500")}}
		assessor := ModelSufficiencyAssessor{Model: model}
		if _, err := assessor.Assess(context.Background(), "q", citations); !errors.Is(err, ErrSufficiencyUnavailable) {
			t.Fatalf("provider failure must be typed unavailable: %v", err)
		}
	})

	t.Run("nil model", func(t *testing.T) {
		assessor := ModelSufficiencyAssessor{}
		if _, err := assessor.Assess(context.Background(), "q", citations); !errors.Is(err, ErrSufficiencyUnavailable) {
			t.Fatalf("missing model must be typed unavailable: %v", err)
		}
	})

	t.Run("context budget exceeded", func(t *testing.T) {
		model := &assessorModel{responses: []Message{{Content: `{"sufficient":true,"missing_facts":[],"reason_code":"covered"}`}}}
		budget := ContextBudget{MaxTokens: 8, ReserveTokens: 1, SystemTokens: 1, ToolSchemaTokens: 1}
		assessor := ModelSufficiencyAssessor{Model: model, Budget: &budget}
		if _, err := assessor.Assess(context.Background(), strings.Repeat("question ", 200), citations); !errors.Is(err, ErrSufficiencyUnavailable) {
			t.Fatalf("context budget overflow must be typed unavailable: %v", err)
		}
		if model.calls != 0 {
			t.Fatalf("assessor must not call the model when evidence does not fit: %d", model.calls)
		}
	})

	t.Run("cancellation", func(t *testing.T) {
		model := &assessorModel{responses: []Message{{Content: `{"sufficient":true,"missing_facts":[],"reason_code":"covered"}`}}}
		assessor := ModelSufficiencyAssessor{Model: model}
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		_, err := assessor.Assess(ctx, "q", citations)
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("cancellation must surface context.Canceled, got %v", err)
		}
		if model.calls != 0 {
			t.Fatalf("cancelled assessor must not call the model: %d", model.calls)
		}
	})
}
