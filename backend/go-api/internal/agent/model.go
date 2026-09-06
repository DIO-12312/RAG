package agent

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type OpenAI struct {
	BaseURL, Key, Name string
	Timeout            time.Duration
	Thinking           bool
	Client             *http.Client
}

func (m OpenAI) Complete(ctx context.Context, messages []Message, force bool) (Message, error) {
	ctx, cancel := context.WithTimeout(ctx, m.Timeout)
	defer cancel()
	choice := any("auto")
	endpoint, _ := url.Parse(m.BaseURL)
	deepseek := strings.HasPrefix(strings.ToLower(m.Name), "deepseek-") || (endpoint != nil && endpoint.Hostname() == "api.deepseek.com")
	if force && !(deepseek && m.Thinking) {
		choice = map[string]any{"type": "function", "function": map[string]string{"name": "rag_retrieve"}}
	}
	payload := map[string]any{"model": m.Name, "messages": messages, "stream": false, "tool_choice": choice, "tools": []any{map[string]any{"type": "function", "function": map[string]any{"name": "rag_retrieve", "description": "Search the user's selected knowledge base. Returns evidence with citation numbers.", "parameters": map[string]any{"type": "object", "properties": map[string]any{"query": map[string]string{"type": "string"}}, "required": []string{"query"}, "additionalProperties": false}}}}, "max_tokens": 4096}
	// Common OpenAI-compatible providers expose this optional extension.
	if deepseek {
		mode := "disabled"
		if m.Thinking {
			mode = "enabled"
		}
		payload["thinking"] = map[string]string{"type": mode}
	} else if m.Thinking {
		payload["enable_thinking"] = true
	}
	b, _ := json.Marshal(payload)
	req, e := http.NewRequestWithContext(ctx, "POST", strings.TrimRight(m.BaseURL, "/")+"/chat/completions", bytes.NewReader(b))
	if e != nil {
		return Message{}, e
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+m.Key)
	client := m.Client
	if client == nil {
		client = &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	}
	resp, e := client.Do(req)
	if e != nil {
		return Message{}, fmt.Errorf("model connection failed")
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return Message{}, fmt.Errorf("model returned HTTP %d", resp.StatusCode)
	}
	var body struct {
		Choices []struct {
			Message Message `json:"message"`
		} `json:"choices"`
	}
	if e = json.NewDecoder(io.LimitReader(resp.Body, 2*1024*1024)).Decode(&body); e != nil {
		return Message{}, fmt.Errorf("invalid model response")
	}
	if len(body.Choices) == 0 {
		return Message{}, fmt.Errorf("empty model choices")
	}
	return body.Choices[0].Message, nil
}
