package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
)

const (
	maxMissingFacts      = 5
	maxMissingFactLength = 256
	maxReasonCodeLength  = 64
)

// ErrSufficiencyUnavailable 表示 assessor 不可用或输出非法。
//
// 调用方必须按降级策略处理：停止额外检索、把现有 Evidence 交给 Finalize 并要求
// 明确说明证据不足；不得据此假定证据充分，也不得据此发起无界重试。
var ErrSufficiencyUnavailable = errors.New("sufficiency assessor unavailable")

// SufficiencyDecision 是 SCA 的结构化输出，不包含自由推理文本。
type SufficiencyDecision struct {
	Sufficient   bool
	MissingFacts []string
	ReasonCode   string
}

// SufficiencyAssessor 判断已检索 Evidence 是否覆盖回答所需事实。
type SufficiencyAssessor interface {
	Assess(context.Context, string, []Citation) (SufficiencyDecision, error)
}

// ModelSufficiencyAssessor 用受限模型实现 SCA；模型调用始终使用 ToolNone。
type ModelSufficiencyAssessor struct {
	Model  Model
	Budget *ContextBudget
}

const sufficiencyPrompt = `You judge whether the supplied evidence covers every fact the user's question needs. ` +
	`Reply with exactly one JSON object and no other text: ` +
	`{"sufficient": true|false, "missing_facts": ["..."], "reason_code": "short_snake_case"}. ` +
	`Use at most 5 missing facts, each at most 256 characters, and an empty array when the evidence is sufficient. ` +
	`Never include reasoning, explanations or markdown fences.`

// Assess 返回结构化判断；非法输出、预算失败与供应商故障都返回分类错误。
func (a ModelSufficiencyAssessor) Assess(ctx context.Context, question string, citations []Citation) (SufficiencyDecision, error) {
	if a.Model == nil {
		return SufficiencyDecision{}, fmt.Errorf("%w: model is not configured", ErrSufficiencyUnavailable)
	}
	if err := ctx.Err(); err != nil {
		return SufficiencyDecision{}, err
	}

	messages := []Message{
		{Role: "system", Content: sufficiencyPrompt},
		{Role: "user", Content: sufficiencyInput(question, citations)},
	}
	if a.Budget != nil {
		messages = a.Budget.TrimMessages(messages)
		if !a.Budget.Fits(messages) {
			return SufficiencyDecision{}, fmt.Errorf("%w: evidence does not fit the context budget", ErrSufficiencyUnavailable)
		}
	}

	msg, err := a.Model.Complete(ctx, messages, ToolPolicy{Mode: ToolNone})
	if err != nil {
		if ctxErr := ctx.Err(); ctxErr != nil {
			return SufficiencyDecision{}, ctxErr
		}
		return SufficiencyDecision{}, fmt.Errorf("%w: %v", ErrSufficiencyUnavailable, err)
	}

	return parseSufficiencyDecision(msg.Content)
}

// sufficiencyInput 把问题与 Evidence 渲染为判断输入；空召回必须显式标注。
func sufficiencyInput(question string, citations []Citation) string {
	var builder strings.Builder
	builder.WriteString("Question:\n")
	builder.WriteString(question)
	builder.WriteString("\n\nEvidence:\n")
	if len(citations) == 0 {
		builder.WriteString("(no evidence retrieved)\n")
	}
	for _, citation := range citations {
		fmt.Fprintf(&builder, "[%d] %s (%s): %s\n", citation.Ordinal, citation.Evidence.SourceName, citation.Evidence.Locator, citation.Evidence.Content)
	}
	return builder.String()
}

// parseSufficiencyDecision 执行严格 JSON 契约：单对象、字段齐全、无未知字段、无围栏与自由文本。
func parseSufficiencyDecision(content string) (SufficiencyDecision, error) {
	trimmed := strings.TrimSpace(content)
	if !strings.HasPrefix(trimmed, "{") || !strings.HasSuffix(trimmed, "}") {
		return SufficiencyDecision{}, fmt.Errorf("%w: decision must be a single JSON object", ErrSufficiencyUnavailable)
	}

	var wire struct {
		Sufficient   *bool     `json:"sufficient"`
		MissingFacts *[]string `json:"missing_facts"`
		ReasonCode   *string   `json:"reason_code"`
	}
	decoder := json.NewDecoder(strings.NewReader(trimmed))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&wire); err != nil {
		return SufficiencyDecision{}, fmt.Errorf("%w: invalid decision json", ErrSufficiencyUnavailable)
	}
	if err := decoder.Decode(&struct{}{}); err != io.EOF {
		return SufficiencyDecision{}, fmt.Errorf("%w: trailing content after decision", ErrSufficiencyUnavailable)
	}
	if wire.Sufficient == nil || wire.MissingFacts == nil || wire.ReasonCode == nil {
		return SufficiencyDecision{}, fmt.Errorf("%w: decision is missing required fields", ErrSufficiencyUnavailable)
	}

	facts := *wire.MissingFacts
	if len(facts) > maxMissingFacts {
		return SufficiencyDecision{}, fmt.Errorf("%w: too many missing facts", ErrSufficiencyUnavailable)
	}
	for _, fact := range facts {
		if strings.TrimSpace(fact) == "" {
			return SufficiencyDecision{}, fmt.Errorf("%w: empty missing fact", ErrSufficiencyUnavailable)
		}
		if len([]rune(fact)) > maxMissingFactLength {
			return SufficiencyDecision{}, fmt.Errorf("%w: missing fact too long", ErrSufficiencyUnavailable)
		}
	}

	reasonCode := strings.TrimSpace(*wire.ReasonCode)
	if reasonCode == "" || len(reasonCode) > maxReasonCodeLength {
		return SufficiencyDecision{}, fmt.Errorf("%w: invalid reason code", ErrSufficiencyUnavailable)
	}

	return SufficiencyDecision{
		Sufficient:   *wire.Sufficient,
		MissingFacts: append([]string(nil), facts...),
		ReasonCode:   reasonCode,
	}, nil
}
