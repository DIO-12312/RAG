package agent

// estimateMessageTokens provides a conservative approximation of the
// number of model tokens consumed by one message.
//
// This is intentionally an estimate rather than tokenizer-exact accounting.
// The context budget will use the same estimator consistently.
func estimateMessageTokens(msg Message) int {
	// Count all model-visible textual fields, not only Content.
	// This is intentionally conservative and tokenizer-independent.
	units := 0

	addText := func(text string) {
		for _, r := range text {
			if r <= 127 {
				units++
			} else {
				units += 2
			}
		}
	}

	addText(msg.Content)
	addText(msg.ReasoningContent)
	addText(msg.ToolCallID)

	for _, call := range msg.ToolCalls {
		addText(call.ID)
		addText(call.Type)
		addText(call.Function.Name)
		addText(call.Function.Arguments)
	}

	tokens := (units + 3) / 4
	if tokens < 1 {
		tokens = 1
	}

	return tokens
}

func estimateContextTokens(messages []Message) int {
	total := 0

	for _, msg := range messages {
		total += estimateMessageTokens(msg)
	}

	return total
}

type ContextBudget struct {
	MaxTokens        int
	ReserveTokens    int
	SystemTokens     int
	ToolSchemaTokens int
}

func (b ContextBudget) Fits(messages []Message) bool {
	used := b.SystemTokens +
		b.ToolSchemaTokens +
		estimateContextTokens(messages)

	return used+b.ReserveTokens <= b.MaxTokens
}

func (b ContextBudget) UsedTokens(messages []Message) int {
	return b.SystemTokens +
		b.ToolSchemaTokens +
		estimateContextTokens(messages)
}

func (b ContextBudget) TrimMessages(messages []Message) []Message {
	if b.Fits(messages) {
		return messages
	}

	if len(messages) <= 2 {
		return messages
	}

	// The first message is the system prompt and must always be preserved.
	// The latest user message is the current question and must always be
	// preserved, even when tool messages follow it.
	latestUser := -1
	for i := len(messages) - 1; i >= 1; i-- {
		if messages[i].Role == "user" {
			latestUser = i
			break
		}
	}

	if latestUser == -1 {
		return []Message{messages[0]}
	}

	// Everything from the latest user message onward belongs to the
	// current interaction and must be preserved. This includes any
	// assistant tool call and its tool result(s).
	tail := append([]Message(nil), messages[latestUser:]...)

	// Older messages can be removed from oldest to newest.
	// Tool-call/result pairs are grouped so they are never split.
	type group struct {
		messages []Message
	}

	groups := make([]group, 0)
	for i := 1; i < latestUser; {
		if messages[i].Role == "assistant" && len(messages[i].ToolCalls) > 0 {
			g := group{messages: []Message{messages[i]}}
			i++

			for i < latestUser && messages[i].Role == "tool" {
				g.messages = append(g.messages, messages[i])
				i++
			}

			groups = append(groups, g)
			continue
		}

		groups = append(groups, group{messages: []Message{messages[i]}})
		i++
	}

	// Remove the oldest history groups until the remaining context fits.
	for len(groups) >= 0 {
		candidate := []Message{messages[0]}
		for _, g := range groups {
			candidate = append(candidate, g.messages...)
		}
		candidate = append(candidate, tail...)

		if b.Fits(candidate) {
			return candidate
		}

		if len(groups) == 0 {
			break
		}
		groups = groups[1:]
	}

	// Even the minimum required context does not fit.
	// Keep the system prompt and current interaction intact; the caller
	// will return an explicit context-budget error.
	return append([]Message{messages[0]}, tail...)
}

func (h Harness) ContextBudget() ContextBudget {
	return ContextBudget{
		MaxTokens:        32768,
		ReserveTokens:    4096,
		SystemTokens:     512,
		ToolSchemaTokens: 512,
	}
}
