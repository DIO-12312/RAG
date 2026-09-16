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

// ordinaryReplyPrefixes 是有限的寒暄/致谢/告别前缀；规范化仅处理标点、空白与
// 常见语气词，不引入新的分类模型。
var ordinaryReplyPrefixes = []string{
	"你好", "您好", "谢谢", "感谢", "多谢", "再见", "拜拜", "辛苦了", "早上好", "晚上好", "哈喽", "hello", "hi",
}

// ordinaryReplyTailLimit 限制社交后缀长度，避免 "hi, how do I configure ..." 这类
// 英文问题被误判为普通交流。
const ordinaryReplyTailLimit = 8

// knowledgeMarkerPattern 标识知识问句或新增事实需求；命中即不得因问候前缀跳过检索。
var knowledgeMarkerPattern = regexp.MustCompile(`(怎么|如何|什么|啥|哪|多少|为什么|是否|另外|还有|顺便|告诉我|帮我|查一下|配置|参数|最大值|最小值|版本|超时|timeout)`)

// selectedKnowledgeBasePattern 表示用户正在询问当前已选择的知识库本身。
// “这个知识库的内容是什么”里的“这个”不是需要回看历史的代词，不能被误判为
// 指代不清而中断检索。
var selectedKnowledgeBasePattern = regexp.MustCompile(`^(这个|当前|本)?(知识库|资料库|文档库|资料)(的)?`)

// normalizeOrdinaryMessage 只做稳定且可解释的规范化：去空白与常见标点，再去掉末尾语气词。
func normalizeOrdinaryMessage(text string) string {
	trimmed := strings.TrimSpace(text)
	trimmed = strings.Map(func(r rune) rune {
		if strings.ContainsRune(" \t\r\n，。！？!?.,;；、~～…:：", r) {
			return -1
		}
		return r
	}, trimmed)
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
	lowered := strings.ToLower(normalized)
	for _, prefix := range ordinaryReplyPrefixes {
		if !strings.HasPrefix(lowered, prefix) {
			continue
		}
		tail := []rune(strings.TrimSpace(strings.TrimPrefix(lowered, prefix)))
		if len(tail) <= ordinaryReplyTailLimit {
			return true
		}
	}
	return false
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

	// 当前知识库是 UI 已明确绑定的对象。即使没有聊天历史，也应检索资料来回答
	// 内容概览、覆盖范围等问题，而不是要求用户再指定对象。
	if selectedKnowledgeBasePattern.MatchString(q) {
		return IntentResult{Intent: "knowledge", Action: "retrieve", StandaloneQuery: q}
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
