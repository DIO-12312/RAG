package agent

import (
	"bufio"
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

	registry := NewToolRegistry()
	if force && !(deepseek && m.Thinking) {
		choice = map[string]any{
			"type":     "function",
			"function": map[string]string{"name": ragRetrieveToolName},
		}
	}

	payload := map[string]any{
		"model":       m.Name,
		"messages":    messages,
		"stream":      false,
		"tool_choice": choice,
		"tools":       registry.OpenAITools(),
		"max_tokens":  4096,
	}
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

// Stream sends a chat completion request with streaming enabled and delivers
// content deltas and fully reassembled tool calls as they arrive.
func (m OpenAI) Stream(
	ctx context.Context,
	messages []Message,
	force bool,
	onDelta func(string) error,
	onToolCall func(ToolCall) error,
) error {
	ctx, cancel := context.WithTimeout(ctx, m.Timeout)
	defer cancel()

	choice := any("auto")
	endpoint, _ := url.Parse(m.BaseURL)
	deepseek := strings.HasPrefix(strings.ToLower(m.Name), "deepseek-") || (endpoint != nil && endpoint.Hostname() == "api.deepseek.com")
	if force && !(deepseek && m.Thinking) {
		choice = map[string]any{"type": "function", "function": map[string]string{"name": "rag_retrieve"}}
	}

	payload := map[string]any{
		"model":       m.Name,
		"messages":    messages,
		"stream":      true,
		"tool_choice": choice,
		"tools": []any{map[string]any{
			"type": "function",
			"function": map[string]any{
				"name":        "rag_retrieve",
				"description": "Search the user's selected knowledge base. Returns evidence with citation numbers.",
				"parameters": map[string]any{
					"type": "object",
					"properties": map[string]any{
						"query": map[string]string{"type": "string"},
					},
					"required":             []string{"query"},
					"additionalProperties": false,
				},
			},
		}},
		"max_tokens": 4096,
	}

	if deepseek {
		mode := "disabled"
		if m.Thinking {
			mode = "enabled"
		}
		payload["thinking"] = map[string]string{"type": mode}
	} else if m.Thinking {
		payload["enable_thinking"] = true
	}

	b, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("marshal model request: %w", err)
	}

	req, err := http.NewRequestWithContext(
		ctx,
		"POST",
		strings.TrimRight(m.BaseURL, "/")+"/chat/completions",
		bytes.NewReader(b),
	)
	if err != nil {
		return err
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+m.Key)
	req.Header.Set("Accept", "text/event-stream")

	client := m.Client
	if client == nil {
		client = &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		}}
	}

	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("model connection failed")
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("model returned HTTP %d", resp.StatusCode)
	}

	type toolState struct {
		call ToolCall
	}

	toolCalls := map[int]*toolState{}

	reader := bufio.NewReader(resp.Body)

	for {
		line, readErr := reader.ReadString('\n')
		if readErr != nil && readErr != io.EOF {
			return fmt.Errorf("read model stream: %w", readErr)
		}

		line = strings.TrimSpace(line)

		if strings.HasPrefix(line, "data:") {
			data := strings.TrimSpace(strings.TrimPrefix(line, "data:"))

			if data == "[DONE]" {
				for _, state := range toolCalls {
					if state.call.Function.Name != "" || state.call.Function.Arguments != "" {
						if err := onToolCall(state.call); err != nil {
							return err
						}
					}
				}
				return nil
			}

			if data != "" {
				var event struct {
					Choices []struct {
						Delta struct {
							Content   string `json:"content"`
							ToolCalls []struct {
								Index    int    `json:"index"`
								ID       string `json:"id"`
								Type     string `json:"type"`
								Function struct {
									Name      string `json:"name"`
									Arguments string `json:"arguments"`
								} `json:"function"`
							} `json:"tool_calls"`
						} `json:"delta"`
					} `json:"choices"`
				}

				if err := json.Unmarshal([]byte(data), &event); err != nil {
					return fmt.Errorf("invalid model stream event")
				}

				for _, choice := range event.Choices {
					if choice.Delta.Content != "" {
						if err := onDelta(choice.Delta.Content); err != nil {
							return err
						}
					}

					for _, tc := range choice.Delta.ToolCalls {
						state, ok := toolCalls[tc.Index]
						if !ok {
							state = &toolState{}
							toolCalls[tc.Index] = state
						}

						if tc.ID != "" {
							state.call.ID = tc.ID
						}
						if tc.Type != "" {
							state.call.Type = tc.Type
						}
						if tc.Function.Name != "" {
							state.call.Function.Name = tc.Function.Name
						}
						if tc.Function.Arguments != "" {
							state.call.Function.Arguments += tc.Function.Arguments
						}
					}
				}
			}
		}

		if readErr == io.EOF {
			for _, state := range toolCalls {
				if state.call.Function.Name != "" || state.call.Function.Arguments != "" {
					if err := onToolCall(state.call); err != nil {
						return err
					}
				}
			}
			return nil
		}
	}
}
