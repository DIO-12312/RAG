package agent

// ToolMode 控制一次模型请求是否向 provider 暴露检索工具。
type ToolMode string

const (
	// ToolNone 完全不暴露 tools/tool_choice；普通交流与已有回答加工必须使用它。
	ToolNone ToolMode = "none"
	// ToolAuto 暴露 rag_retrieve，由模型决定是否调用。
	ToolAuto ToolMode = "auto"
	// ToolRequired 暴露 rag_retrieve，并把 tool_choice 指向它。
	ToolRequired ToolMode = "required"
)

// ToolPolicy 是意图到 provider 请求的唯一映射结果。
type ToolPolicy struct {
	Mode         ToolMode
	RequiredName string
	// Temperature is optional so ordinary answer generation keeps the provider
	// default while structured control decisions can request deterministic output.
	Temperature *float64
}

// DeterministicToolNone is used for structured JSON decisions such as query
// rewriting and evidence sufficiency assessment.
func DeterministicToolNone() ToolPolicy {
	temperature := 0.0
	return ToolPolicy{Mode: ToolNone, Temperature: &temperature}
}

// PolicyForIntent 返回零基 round 对应的工具策略。
//
// reply/reuse 在任何轮次都不暴露工具；retrieve 首轮强制调用、后续交给模型决定；
// 其余动作保守返回 ToolNone，由 Harness 在调用模型前用 knownIntentAction 拒绝，
// 绝不默认直接回答。
func PolicyForIntent(intent IntentResult, round int) ToolPolicy {
	switch intent.Action {
	case "reply", "reuse":
		return ToolPolicy{Mode: ToolNone}
	case "retrieve":
		if round == 0 {
			return ToolPolicy{Mode: ToolRequired, RequiredName: "rag_retrieve"}
		}
		return ToolPolicy{Mode: ToolAuto}
	default:
		return ToolPolicy{Mode: ToolNone}
	}
}

// knownIntentAction 只接受路由明确产出的动作，避免未知动作被当作直接回答。
func knownIntentAction(action string) bool {
	switch action {
	case "reply", "reuse", "retrieve", "clarify":
		return true
	default:
		return false
	}
}
