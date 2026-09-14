package agent

import "testing"

func TestToolRegistryValidateCall(t *testing.T) {
	registry := NewToolRegistry()

	valid := ToolCall{ID: "call-1", Type: "function"}
	valid.Function.Name = ragRetrieveToolName
	valid.Function.Arguments = `{"query":"如何配置超时？"}`

	query, err := registry.ValidateCall(valid)
	if err != nil {
		t.Fatalf("valid tool call rejected: %v", err)
	}
	if query != "如何配置超时？" {
		t.Fatalf("unexpected query: %q", query)
	}
}

func TestToolRegistryRejectsUnsafeCalls(t *testing.T) {
	registry := NewToolRegistry()

	tests := []struct {
		name string
		call ToolCall
	}{
		{
			name: "missing call id",
			call: ToolCall{
				Type: "function",
				Function: struct {
					Name      string `json:"name"`
					Arguments string `json:"arguments"`
				}{Name: ragRetrieveToolName, Arguments: `{"query":"test"}`},
			},
		},
		{
			name: "unknown tool",
			call: ToolCall{
				ID:   "call-2",
				Type: "function",
				Function: struct {
					Name      string `json:"name"`
					Arguments string `json:"arguments"`
				}{Name: "shell_exec", Arguments: `{"query":"test"}`},
			},
		},
		{
			name: "invalid json",
			call: ToolCall{
				ID:   "call-3",
				Type: "function",
				Function: struct {
					Name      string `json:"name"`
					Arguments string `json:"arguments"`
				}{Name: ragRetrieveToolName, Arguments: `{invalid`},
			},
		},
		{
			name: "empty query",
			call: ToolCall{
				ID:   "call-4",
				Type: "function",
				Function: struct {
					Name      string `json:"name"`
					Arguments string `json:"arguments"`
				}{Name: ragRetrieveToolName, Arguments: `{"query":"   "}`},
			},
		},
		{
			name: "unsupported call type",
			call: ToolCall{
				ID:   "call-5",
				Type: "computer",
				Function: struct {
					Name      string `json:"name"`
					Arguments string `json:"arguments"`
				}{Name: ragRetrieveToolName, Arguments: `{"query":"test"}`},
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if _, err := registry.ValidateCall(tt.call); err == nil {
				t.Fatalf("expected validation error")
			}
		})
	}
}

func TestToolRegistryRejectsOversizedArguments(t *testing.T) {
	registry := NewToolRegistry()

	call := ToolCall{ID: "call-large", Type: "function"}
	call.Function.Name = ragRetrieveToolName
	call.Function.Arguments = `{"query":"` + string(make([]byte, 4090)) + `"}`

	if _, err := registry.ValidateCall(call); err == nil {
		t.Fatalf("expected oversized argument error")
	}
}

func TestToolRegistryOpenAITools(t *testing.T) {
	registry := NewToolRegistry()
	tools := registry.OpenAITools()

	if len(tools) != 1 {
		t.Fatalf("expected 1 tool, got %d", len(tools))
	}

	tool, ok := tools[0].(map[string]any)
	if !ok {
		t.Fatalf("unexpected tool type: %T", tools[0])
	}
	if tool["type"] != "function" {
		t.Fatalf("unexpected tool type: %v", tool["type"])
	}

	function, ok := tool["function"].(map[string]any)
	if !ok {
		t.Fatalf("unexpected function type: %T", tool["function"])
	}
	if function["name"] != ragRetrieveToolName {
		t.Fatalf("unexpected function name: %v", function["name"])
	}
	if function["description"] == "" {
		t.Fatalf("tool description is empty")
	}
	if function["parameters"] == nil {
		t.Fatalf("tool parameters are missing")
	}
}

func TestToolRegistryRejectsUnknownArguments(t *testing.T) {
	registry := NewToolRegistry()

	call := ToolCall{ID: "call-unknown", Type: "function"}
	call.Function.Name = ragRetrieveToolName
	call.Function.Arguments = `{"query":"test","unexpected":"value"}`

	if _, err := registry.ValidateCall(call); err == nil {
		t.Fatalf("expected unknown argument to be rejected")
	}
}
