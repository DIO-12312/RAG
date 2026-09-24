package agent

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"log/slog"
	"rag-mvp/backend/go-api/internal/telemetry"
)

// Run 观测阶段；成功路径按 route → model → tool → assess → finalize → complete 顺序出现。
const (
	RunStageRoute    = "route"
	RunStageModel    = "model"
	RunStageTool     = "tool"
	RunStageAssess   = "assess"
	RunStageRewrite  = "rewrite"
	RunStageFinalize = "finalize"
	RunStageComplete = "complete"
)

// RunEvent 是脱敏的 Run 观测事件：只包含阶段、计数、耗时与终止原因，
// 不含问题原文、Evidence 正文、模型私有推理、工具原始参数或任何凭据。
type RunEvent struct {
	RunID          string
	Stage          string
	Round          int
	Action         string
	QueryHash      string
	EvidenceCount  int
	ModelCalls     int
	RetrievalCalls int
	RewriteCalls   int
	DurationMS     int64
	ErrorCode      string
	StopReason     StopReason
}

// Observer 接收 Run 事件；实现必须保证轻量，且不得因自身失败影响 Run 结果。
type Observer interface {
	Observe(context.Context, RunEvent)
}

// NewRunID 生成 32 位十六进制 run ID。
func NewRunID() string {
	buffer := make([]byte, 16)
	if _, err := rand.Read(buffer); err != nil {
		return "run-unknown"
	}
	return hex.EncodeToString(buffer)
}

// QueryFingerprint 返回查询的 SHA-256 前 16 位十六进制与字节长度，用于观测而不泄露原文。
func QueryFingerprint(query string) (string, int) {
	sum := sha256.Sum256([]byte(query))
	return hex.EncodeToString(sum[:])[:16], len([]byte(query))
}

// JSONLogObserver 使用标准库 slog 输出单行结构化事件。
type JSONLogObserver struct {
	Logger *slog.Logger
}

// Observe 只输出脱敏字段；日志设施自身的失败不会回传给 Agent。
func (o JSONLogObserver) Observe(ctx context.Context, event RunEvent) {
	logger := o.Logger
	if logger == nil {
		logger = slog.Default()
	}
	traceID, spanID := telemetry.IDs(ctx)
	logger.Info("agent_run",
		"run_id", event.RunID,
		"trace_id", traceID,
		"span_id", spanID,
		"stage", event.Stage,
		"round", event.Round,
		"action", event.Action,
		"query_hash", event.QueryHash,
		"evidence", event.EvidenceCount,
		"model_calls", event.ModelCalls,
		"retrieval_calls", event.RetrievalCalls,
		"rewrite_calls", event.RewriteCalls,
		"duration_ms", event.DurationMS,
		"error_code", event.ErrorCode,
		"stop_reason", string(event.StopReason),
	)
}

// errorCodeForStopReason 把终止原因映射为稳定错误码；成功路径为空字符串。
func errorCodeForStopReason(reason StopReason) string {
	switch reason {
	case StopReasonCancelled:
		return "cancelled"
	case StopReasonProviderError:
		return "provider_error"
	case StopReasonInvalidToolCall:
		return "invalid_tool_call"
	case StopReasonBudgetExceeded:
		return "budget_exceeded"
	default:
		return ""
	}
}
