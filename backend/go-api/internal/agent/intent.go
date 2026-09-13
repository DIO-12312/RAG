package agent

import (
	"regexp"
	"strings"
)

type IntentResult struct {
	Intent                string
	Action                string
	StandaloneQuery       string
	ClarificationQuestion string
}

// ordinaryReplyPattern 只匹配"整条消息就是寒暄/致谢/告别"的情况。
// 规范化仅处理空白、末尾标点与常见语气词，不引入新的分类模型。
var ordinaryReplyPattern = regexp.MustCompile(`^(你好|您好|谢谢|感谢|多谢|再见|拜拜|辛苦了|早上好|晚上好|哈喽|hello|hi)(你|您|啦|了|啊|呀|哈)?$`)

// knowledgeMarkerPattern 标识知识问句或新增事实需求；命中即不得因问候前缀跳过检索。
var knowledgeMarkerPattern = regexp.MustCompile(`(怎么|如何|什么|啥|哪|多少|为什么|是否|另外|还有|顺便|告诉我|帮我|查一下|配置|参数|最大值|最小值|版本|超时|timeout)`)

// normalizeOrdinaryMessage 只做稳定且可解释的规范化：去首尾空白、末尾标点与语气词。
func normalizeOrdinaryMessage(text string) string {
	trimmed := strings.TrimSpace(text)
	trimmed = strings.TrimRight(trimmed, " \t\r\n，。！？!?.,;；、~～…")
	trimmed = strings.TrimRight(trimmed, "啊呀吧哦呢哈嘛啦喔哟吗")
	return strings.TrimSpace(trimmed)
}

// isOrdinaryReply 判断整条消息是否只表达普通交流。
func isOrdinaryReply(text string) bool {
	normalized := normalizeOrdinaryMessage(text)
	if normalized == "" {
		return false
	}
	if knowledgeMarkerPattern.MatchString(normalized) {
		// 同一消息混有知识问句或新增事实标记时，必须继续检索。
		return false
	}
	return ordinaryReplyPattern.MatchString(strings.ToLower(normalized))
}

func RouteIntent(question string, history []Message) IntentResult {
	q := strings.TrimSpace(question)

	// 普通交流（规范化末尾语气词与标点；混合事实问题不会命中整条匹配）
	if isOrdinaryReply(q) {
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
