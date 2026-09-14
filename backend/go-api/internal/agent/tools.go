package agent

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"strings"
)

const ragRetrieveToolName = "rag_retrieve"

type ToolDefinition struct {
	Name        string
	Description string
	Parameters  map[string]any
}

type ToolRegistry struct {
	tools map[string]ToolDefinition
}

func NewToolRegistry() *ToolRegistry {
	return &ToolRegistry{
		tools: map[string]ToolDefinition{
			ragRetrieveToolName: {
				Name:        ragRetrieveToolName,
				Description: "Search the user's selected knowledge base. Returns evidence with citation numbers.",
				Parameters: map[string]any{
					"type": "object",
					"properties": map[string]any{
						"query": map[string]string{
							"type": "string",
						},
					},
					"required":             []string{"query"},
					"additionalProperties": false,
				},
			},
		},
	}
}

func (r *ToolRegistry) Get(name string) (ToolDefinition, bool) {
	if r == nil {
		return ToolDefinition{}, false
	}
	tool, ok := r.tools[name]
	return tool, ok
}

func (r *ToolRegistry) OpenAITools() []any {
	if r == nil {
		return nil
	}

	result := make([]any, 0, len(r.tools))
	for _, tool := range r.tools {
		result = append(result, map[string]any{
			"type": "function",
			"function": map[string]any{
				"name":        tool.Name,
				"description": tool.Description,
				"parameters":  tool.Parameters,
			},
		})
	}
	return result
}

func (r *ToolRegistry) ValidateCall(call ToolCall) (string, error) {
	if call.ID == "" {
		return "", errors.New("tool call id is empty")
	}
	if call.Type != "" && call.Type != "function" {
		return "", errors.New("unsupported tool call type")
	}

	tool, ok := r.Get(call.Function.Name)
	if !ok {
		return "", errors.New("unknown tool")
	}

	if len(call.Function.Arguments) > 8192 {
		return "", errors.New("invalid tool arguments")
	}

	var args struct {
		Query string `json:"query"`
	}

	decoder := json.NewDecoder(strings.NewReader(call.Function.Arguments))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&args); err != nil {
		return "", errors.New("invalid tool arguments")
	}

	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return "", errors.New("invalid tool arguments")
	}
	if strings.TrimSpace(args.Query) == "" || len(args.Query) > 4096 {
		return "", errors.New("invalid tool arguments")
	}

	if tool.Name != ragRetrieveToolName {
		return "", errors.New("unsupported tool")
	}

	return args.Query, nil
}

type ToolExecutor interface {
	Execute(context.Context, string, string, int) ([]Evidence, error)
}
