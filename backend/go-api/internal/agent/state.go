package agent

import (
	"errors"
	"fmt"
)

// RunPhase 是 Agent Run 的显式相位。纯类型：本文件不得调用 HTTP、gRPC 或模型 SDK。
type RunPhase string

const (
	RunPhaseRoute    RunPhase = "route"
	RunPhaseModel    RunPhase = "model"
	RunPhaseTool     RunPhase = "tool"
	RunPhaseAssess   RunPhase = "assess"
	RunPhaseRewrite  RunPhase = "rewrite"
	RunPhaseFinalize RunPhase = "finalize"
	RunPhaseDone     RunPhase = "done"
	RunPhaseFailed   RunPhase = "failed"
)

// StopReason 是一次 Run 的终止原因，用于观测与评测。
type StopReason string

const (
	StopReasonCompleted            StopReason = "completed"
	StopReasonDirectReply          StopReason = "direct_reply"
	StopReasonClarification        StopReason = "clarification"
	StopReasonEvidenceSufficient   StopReason = "evidence_sufficient"
	StopReasonEvidenceInsufficient StopReason = "evidence_insufficient"
	StopReasonBudgetExceeded       StopReason = "budget_exceeded"
	StopReasonCancelled            StopReason = "cancelled"
	StopReasonProviderError        StopReason = "provider_error"
	StopReasonInvalidToolCall      StopReason = "invalid_tool_call"
)

var (
	// ErrInvalidTransition 表示相位迁移不在允许集合内。
	ErrInvalidTransition = errors.New("invalid run phase transition")
	// ErrBudgetExceeded 表示某个预算在动作开始前已被用完。
	ErrBudgetExceeded = errors.New("run budget exceeded")
	// ErrRunTerminal 表示 Run 已进入终态，不允许重新打开。
	ErrRunTerminal = errors.New("run already reached a terminal phase")
)

// RunLimits 集中定义一次 Run 的全部预算。
type RunLimits struct {
	MaxRetrievalRounds   int
	MaxRewriteRounds     int
	MaxToolCallsPerRound int
	MaxEvidence          int
	MaxToolOutputBytes   int
}

// DefaultRunLimits 保留既有对外行为并补齐检索/改写轮次上限。
func DefaultRunLimits() RunLimits {
	return RunLimits{
		// One initial query plus two rewrites with at most two subqueries each.
		// RetrievalRounds counts actual tool calls, so three truncated the second
		// rewrite halfway through and made outcomes depend on query ordering.
		MaxRetrievalRounds:   5,
		MaxRewriteRounds:     2,
		MaxToolCallsPerRound: 4,
		MaxEvidence:          40,
		MaxToolOutputBytes:   128 * 1024,
	}
}

// RunState 是执行器持有的全部可变状态。
type RunState struct {
	Phase            RunPhase
	Intent           IntentResult
	Messages         []Message
	Limits           RunLimits
	ModelCalls       int
	RetrievalRounds  int
	RewriteRounds    int
	AttemptedQueries []string
	Evidence         []Citation
	StopReason       StopReason

	// 运行上下文：由 Harness 在构造 RunState 时填充。
	Dataset   string
	Question  string
	History   []Message
	TopK      int
	Budget    ContextBudget
	Streaming bool
	Pool      *EvidencePool
	Final     Message
	ToolCalls []ToolCall
	Answer    string
	Citations []Citation

	// 充分性判断结果：SufficiencyChecked 表示本次 Run 真正执行过 SCA（或已降级）。
	Sufficiency        SufficiencyDecision
	SufficiencyChecked bool
	AnswerNeeded       bool

	// ToolReason 标记本轮工具调用的来源（model/rewrite），用于 SSE 的 reason 字段。
	ToolReason string

	// 上下文占用汇报：记录上次发给前端的用量，避免重复事件。
	ContextReported bool
	ContextTokens   int
	ContextEvidence int
}

// queryAttempted 判断查询是否已在本次 Run 的尝试账本中（按规范化文本比较）。
func (s *RunState) queryAttempted(query string) bool {
	normalized := NormalizeAttemptedQuery(query)
	for _, attempted := range s.AttemptedQueries {
		if NormalizeAttemptedQuery(attempted) == normalized {
			return true
		}
	}
	return false
}

// NewRunState 构造处于 Route 相位的 Run。
func NewRunState(limits RunLimits, intent IntentResult, messages []Message) *RunState {
	return &RunState{Phase: RunPhaseRoute, Intent: intent, Messages: messages, Limits: limits}
}

// allowedTransitions 固定合法主路径；终态没有任何出边。
var allowedTransitions = map[RunPhase]map[RunPhase]bool{
	RunPhaseRoute:    {RunPhaseModel: true, RunPhaseDone: true, RunPhaseFailed: true},
	RunPhaseModel:    {RunPhaseTool: true, RunPhaseFinalize: true, RunPhaseDone: true, RunPhaseFailed: true},
	RunPhaseTool:     {RunPhaseModel: true, RunPhaseAssess: true, RunPhaseFinalize: true, RunPhaseFailed: true},
	RunPhaseAssess:   {RunPhaseRewrite: true, RunPhaseTool: true, RunPhaseModel: true, RunPhaseFinalize: true, RunPhaseFailed: true},
	RunPhaseRewrite:  {RunPhaseTool: true, RunPhaseFinalize: true, RunPhaseFailed: true},
	RunPhaseFinalize: {RunPhaseDone: true, RunPhaseFailed: true},
	RunPhaseDone:     {},
	RunPhaseFailed:   {},
}

// Stopped 报告 Run 是否已进入终态。
func (s *RunState) Stopped() bool {
	return s.Phase == RunPhaseDone || s.Phase == RunPhaseFailed
}

func (s *RunState) ensureActive() error {
	if s.Stopped() {
		return fmt.Errorf("%w: %s", ErrRunTerminal, s.Phase)
	}
	return nil
}

// TransitionTo 校验并执行相位迁移；终态不可重开。
func (s *RunState) TransitionTo(next RunPhase) error {
	if err := s.ensureActive(); err != nil {
		return err
	}
	if !allowedTransitions[s.Phase][next] {
		return fmt.Errorf("%w: %s -> %s", ErrInvalidTransition, s.Phase, next)
	}
	s.Phase = next
	return nil
}

// RecordModelCall 记录一次成功的模型调用，仅用于策略选择与可观测性，不作为硬上限。
func (s *RunState) RecordModelCall() { s.ModelCalls++ }

// CheckRetrievalRound 在开始新一轮检索前校验预算。
func (s *RunState) CheckRetrievalRound() error {
	if err := s.ensureActive(); err != nil {
		return err
	}
	if s.RetrievalRounds >= s.Limits.MaxRetrievalRounds {
		return fmt.Errorf("%w: retrieval rounds reached %d", ErrBudgetExceeded, s.Limits.MaxRetrievalRounds)
	}
	return nil
}

// RecordRetrievalRound 记录一次成功的检索轮次。
func (s *RunState) RecordRetrievalRound() { s.RetrievalRounds++ }

// CheckRewriteRound 在开始一轮改写前校验预算。
func (s *RunState) CheckRewriteRound() error {
	if err := s.ensureActive(); err != nil {
		return err
	}
	if s.RewriteRounds >= s.Limits.MaxRewriteRounds {
		return fmt.Errorf("%w: rewrite rounds reached %d", ErrBudgetExceeded, s.Limits.MaxRewriteRounds)
	}
	return nil
}

// RecordRewriteRound 记录一次成功的改写轮次。
func (s *RunState) RecordRewriteRound() { s.RewriteRounds++ }

// CheckToolCalls 校验单轮工具调用数量。
func (s *RunState) CheckToolCalls(count int) error {
	if err := s.ensureActive(); err != nil {
		return err
	}
	if count > s.Limits.MaxToolCallsPerRound {
		return fmt.Errorf("%w: tool calls %d > %d", ErrBudgetExceeded, count, s.Limits.MaxToolCallsPerRound)
	}
	return nil
}

// CheckEvidence 校验加入 additional 条 Evidence 后是否超限。
func (s *RunState) CheckEvidence(additional int) error {
	if err := s.ensureActive(); err != nil {
		return err
	}
	if len(s.Evidence)+additional > s.Limits.MaxEvidence {
		return fmt.Errorf("%w: evidence %d + %d > %d", ErrBudgetExceeded, len(s.Evidence), additional, s.Limits.MaxEvidence)
	}
	return nil
}

// MarkStopped 以成功语义终止 Run；已终止的 Run 保持首个原因。
func (s *RunState) MarkStopped(reason StopReason) {
	if s.Stopped() {
		return
	}
	s.StopReason = reason
	s.Phase = RunPhaseDone
}

// MarkFailed 以失败语义终止 Run；已终止的 Run 保持首个原因。
func (s *RunState) MarkFailed(reason StopReason) {
	if s.Stopped() {
		return
	}
	s.StopReason = reason
	s.Phase = RunPhaseFailed
}
