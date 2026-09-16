package agent

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func rewriteRequest() RewriteRequest {
	return RewriteRequest{
		OriginalQuestion:   "timeout 最大是多少？",
		StandaloneQuestion: "timeout 最大是多少？",
		MissingFacts:       []string{"timeout 的最大允许值"},
		AttemptedQueries:   []string{"timeout 最大是多少？"},
	}
}

func TestNormalizeAttemptedQueryStabilizesDedup(t *testing.T) {
	if NormalizeAttemptedQuery("  Timeout   最大 值 ") != NormalizeAttemptedQuery("timeout 最大 值") {
		t.Fatal("normalization must ignore case and whitespace runs")
	}
	if NormalizeAttemptedQuery("timeout 最大值") == NormalizeAttemptedQuery("timeout 最小值") {
		t.Fatal("normalization must not collapse different queries")
	}
}

func TestModelQueryRewriterSingleGapProducesOneQuery(t *testing.T) {
	model := &assessorModel{responses: []Message{{Content: `{"queries":["timeout 的最大允许值和单位"]}`}}}
	rewriter := ModelQueryRewriter{Model: model}

	result, err := rewriter.Rewrite(context.Background(), rewriteRequest())
	if err != nil {
		t.Fatalf("rewrite failed: %v", err)
	}
	if len(result.Queries) != 1 || result.Queries[0] != "timeout 的最大允许值和单位" {
		t.Fatalf("unexpected queries: %+v", result)
	}
	if model.policies[0].Mode != ToolNone || model.policies[0].Temperature == nil || *model.policies[0].Temperature != 0 {
		t.Fatalf("rewriter must use deterministic ToolNone, got %+v", model.policies[0])
	}
	request := model.messages[0][len(model.messages[0])-1].Content
	for _, want := range []string{"timeout 最大是多少？", "timeout 的最大允许值", "timeout 最大是多少？"} {
		if !strings.Contains(request, want) {
			t.Fatalf("rewrite request missing %q:\n%s", want, request)
		}
	}
}

func TestModelQueryRewriterComparisonKeepsBothSubjects(t *testing.T) {
	model := &assessorModel{responses: []Message{{Content: `{"queries":["A 与 B 的默认值分别是多少"]}`}}}
	rewriter := ModelQueryRewriter{Model: model}

	request := RewriteRequest{
		OriginalQuestion:   "A 与 B 的默认值差多少？",
		StandaloneQuestion: "A 与 B 的默认值差多少？",
		MissingFacts:       []string{"B 的默认值"},
		AttemptedQueries:   []string{"A 与 B 的默认值差多少？"},
	}
	result, err := rewriter.Rewrite(context.Background(), request)
	if err != nil {
		t.Fatalf("rewrite failed: %v", err)
	}
	if len(result.Queries) != 1 || !strings.Contains(result.Queries[0], "A") || !strings.Contains(result.Queries[0], "B") {
		t.Fatalf("comparison rewrite must keep both subjects: %+v", result)
	}
}

func TestModelQueryRewriterAllowsAuditedDDSEntityNormalization(t *testing.T) {
	tests := []struct {
		name     string
		question string
		query    string
	}{
		{name: "DW abbreviation", question: "DW 如何设置可靠传输？", query: "DataWriter 可靠传输 QoS 设置"},
		{name: "Chinese writer wording", question: "发布端怎么等待对端确认？", query: "DataWriter wait_for_acknowledgments DDS"},
		{name: "service quality wording", question: "服务质量策略什么时候可以修改？", query: "QoS Quality of Service 修改时机"},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			model := &assessorModel{responses: []Message{{Content: `{"queries":["` + tc.query + `"]}`}}}
			rewriter := ModelQueryRewriter{Model: model}
			request := RewriteRequest{
				OriginalQuestion:   tc.question,
				StandaloneQuestion: tc.question,
				MissingFacts:       []string{"需要补充核心配置事实"},
				AttemptedQueries:   []string{tc.question},
			}

			result, err := rewriter.Rewrite(context.Background(), request)
			if err != nil {
				t.Fatalf("controlled DDS normalization was rejected: %v", err)
			}
			if len(result.Queries) != 1 || result.Queries[0] != tc.query {
				t.Fatalf("unexpected controlled rewrite: %+v", result)
			}
		})
	}
}

func TestModelQueryRewriterAuditsEveryIntroducedEntity(t *testing.T) {
	request := RewriteRequest{
		OriginalQuestion:   "DW 如何设置可靠传输？",
		StandaloneQuestion: "DW 如何设置可靠传输？",
		MissingFacts:       []string{"Project Cobalt 的 WriterPro 配置"},
		AttemptedQueries:   []string{"DW 如何设置可靠传输？"},
	}

	t.Run("controlled DataWriter is allowed but guessed product is rejected", func(t *testing.T) {
		model := &assessorModel{responses: []Message{{Content: `{"queries":["Project Cobalt DataWriter 可靠传输"]}`}}}
		rewriter := ModelQueryRewriter{Model: model}
		if _, err := rewriter.Rewrite(context.Background(), request); !errors.Is(err, ErrRewriteUnavailable) {
			t.Fatalf("guessed product from missing facts must be rejected: %v", err)
		}
	})

	t.Run("unknown API is rejected even when another entity is approved", func(t *testing.T) {
		model := &assessorModel{responses: []Message{{Content: `{"queries":["DataWriter WriterPro 可靠传输"]}`}}}
		rewriter := ModelQueryRewriter{Model: model}
		if _, err := rewriter.Rewrite(context.Background(), request); !errors.Is(err, ErrRewriteUnavailable) {
			t.Fatalf("unapproved API must be rejected: %v", err)
		}
	})
}

func TestModelQueryRewriterDeduplicatesAttemptedQueries(t *testing.T) {
	cases := []struct {
		name      string
		response  string
		want      []string
		wantNoNew bool
	}{
		{
			name:      "repeat of attempted query converges",
			response:  `{"queries":["timeout 最大是多少？"]}`,
			wantNoNew: true,
		},
		{
			name:     "duplicate inside response collapses",
			response: `{"queries":["timeout 上限","timeout 上限"]}`,
			want:     []string{"timeout 上限"},
		},
		{
			name:      "empty list converges",
			response:  `{"queries":[]}`,
			wantNoNew: true,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			model := &assessorModel{responses: []Message{{Content: tc.response}}}
			rewriter := ModelQueryRewriter{Model: model}
			result, err := rewriter.Rewrite(context.Background(), rewriteRequest())
			if tc.wantNoNew {
				if !errors.Is(err, ErrNoNewQuery) {
					t.Fatalf("expected ErrNoNewQuery, got %v (%+v)", err, result)
				}
				return
			}
			if err != nil {
				t.Fatalf("rewrite failed: %v", err)
			}
			if len(result.Queries) != len(tc.want) {
				t.Fatalf("queries = %+v, want %+v", result.Queries, tc.want)
			}
			for i, want := range tc.want {
				if result.Queries[i] != want {
					t.Fatalf("query %d = %q, want %q", i, result.Queries[i], want)
				}
			}
		})
	}
}

func TestModelQueryRewriterStrictContract(t *testing.T) {
	long := strings.Repeat("x", 4097)
	invalid := []struct {
		name     string
		response string
	}{
		{"missing queries", `{"missing_facts":[]}`},
		{"unknown field", `{"queries":["q"],"reasoning":"because"}`},
		{"markdown fence", "```json\n{\"queries\":[\"q\"]}\n```"},
		{"trailing prose", `{"queries":["q"]} 以上`},
		{"blank query", `{"queries":["   "]}`},
		{"too many queries", `{"queries":["a","b","c"]}`},
		{"query too long", `{"queries":["` + long + `"]}`},
		{"new entity not in question", `{"queries":["Project Cobalt 的 launch code"]}`},
		{"empty body", ``},
	}

	for _, tc := range invalid {
		t.Run(tc.name, func(t *testing.T) {
			model := &assessorModel{responses: []Message{{Content: tc.response}}}
			rewriter := ModelQueryRewriter{Model: model}
			if _, err := rewriter.Rewrite(context.Background(), rewriteRequest()); !errors.Is(err, ErrRewriteUnavailable) {
				t.Fatalf("invalid rewrite accepted: %v", err)
			}
		})
	}

	t.Run("missing model", func(t *testing.T) {
		rewriter := ModelQueryRewriter{}
		if _, err := rewriter.Rewrite(context.Background(), rewriteRequest()); !errors.Is(err, ErrRewriteUnavailable) {
			t.Fatalf("missing model must be typed unavailable: %v", err)
		}
	})

	t.Run("provider failure", func(t *testing.T) {
		model := &assessorModel{errs: []error{errors.New("model returned HTTP 500")}}
		rewriter := ModelQueryRewriter{Model: model}
		if _, err := rewriter.Rewrite(context.Background(), rewriteRequest()); !errors.Is(err, ErrRewriteUnavailable) {
			t.Fatalf("provider failure must be typed unavailable: %v", err)
		}
	})

	t.Run("cancellation", func(t *testing.T) {
		model := &assessorModel{responses: []Message{{Content: `{"queries":["q"]}`}}}
		rewriter := ModelQueryRewriter{Model: model}
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		if _, err := rewriter.Rewrite(ctx, rewriteRequest()); !errors.Is(err, context.Canceled) {
			t.Fatalf("cancellation must surface context.Canceled, got %v", err)
		}
		if model.calls != 0 {
			t.Fatalf("cancelled rewriter must not call the model: %d", model.calls)
		}
	})
}
