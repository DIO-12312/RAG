package agent

import "strings"

type IntentResult struct {
	Intent                string
	Action                string
	StandaloneQuery       string
	ClarificationQuestion string
}

func RouteIntent(question string, history []Message) IntentResult {
	q := strings.TrimSpace(question)

	// 普通交流
	if q == "你好" || q == "您好" || q == "谢谢" || q == "感谢" {
		return IntentResult{
			Intent: "ordinary",
			Action: "reply",
		}
	}

	// 已有回答加工
	// 已有回答加工
	if strings.Contains(q, "上一条") &&
		(strings.Contains(q, "总结") ||
			strings.Contains(q, "简化") ||
			strings.Contains(q, "改写") ||
			strings.Contains(q, "整理")) {

		// 如果同时包含明确的新事实问题，则必须重新检索。
		if strings.Contains(q, "另外") ||
			strings.Contains(q, "告诉我") ||
			strings.Contains(q, "多少") ||
			strings.Contains(q, "怎么") ||
			strings.Contains(q, "什么") ||
			strings.Contains(q, "哪里") {
			return IntentResult{
				Intent:          "knowledge",
				Action:          "retrieve",
				StandaloneQuery: q,
			}
		}

		return IntentResult{
			Intent: "transform",
			Action: "reuse",
		}
	}

	// 上下文追问 / 歧义指代
	if strings.HasPrefix(q, "那") ||
		strings.HasPrefix(q, "它") ||
		strings.HasPrefix(q, "这个") ||
		strings.HasPrefix(q, "这种") {

		previousQuestion := ""
		for i := len(history) - 1; i >= 0; i-- {
			if history[i].Role == "user" {
				previousQuestion = strings.TrimSpace(history[i].Content)
				break
			}
		}

		if previousQuestion == "" {
			return IntentResult{
				Intent:                "follow_up",
				Action:                "clarify",
				ClarificationQuestion: "请问你指的是哪个对象或配置？",
			}
		}

		return IntentResult{
			Intent:          "follow_up",
			Action:          "retrieve",
			StandaloneQuery: previousQuestion + q,
		}
	}

	// 默认：知识库问答
	return IntentResult{
		Intent:          "knowledge",
		Action:          "retrieve",
		StandaloneQuery: q,
	}
}
