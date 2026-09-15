package agent

import "errors"

// RunError 携带终止原因，便于调用方映射稳定错误码与用户提示。
type RunError struct {
	Reason StopReason
	Err    error
}

func (e *RunError) Error() string { return e.Err.Error() }

func (e *RunError) Unwrap() error { return e.Err }

// FailureHint 把 Run 失败映射为稳定的对外错误码与提示。
//
// 预算或工具契约类失败此前统一退化成"请检查模型连通性"，把可收敛的问题说成
// 连通性问题；这里按终止原因给出可操作的提示。
func FailureHint(err error) (string, string) {
	var runErr *RunError
	if errors.As(err, &runErr) {
		switch runErr.Reason {
		case StopReasonBudgetExceeded:
			return "RUN_BUDGET_EXCEEDED", "本次提问超出检索、工具或上下文预算，请重试或换一种问法。"
		case StopReasonInvalidToolCall:
			return "TOOL_CALL_INVALID", "模型返回了不受支持的工具调用或引用，请重试或更换模型。"
		case StopReasonProviderError:
			return "MODEL_UNAVAILABLE", "模型服务不可用或返回异常，请检查模型配置与连通性。"
		case StopReasonCancelled:
			return "REQUEST_CANCELLED", "请求已取消。"
		}
	}
	return "CHAT_FAILED", "问答未完成，请检查模型连通性、工具调用支持及知识库状态。"
}
