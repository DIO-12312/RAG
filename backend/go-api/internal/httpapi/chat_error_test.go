package httpapi

import (
	"errors"
	"testing"

	"rag-mvp/backend/go-api/internal/agent"
)

func TestChatErrorResponse(t *testing.T) {
	tests := []struct {
		name string
		err  error
		code string
	}{
		{
			name: "model",
			err:  agent.NewAgentError(agent.ErrorClassModel, errors.New("model failed")),
			code: "MODEL_FAILED",
		},
		{
			name: "tool",
			err:  agent.NewAgentError(agent.ErrorClassTool, errors.New("tool failed")),
			code: "TOOL_FAILED",
		},
		{
			name: "retrieval",
			err:  agent.NewAgentError(agent.ErrorClassRetrieval, errors.New("retrieval failed")),
			code: "RETRIEVAL_FAILED",
		},
		{
			name: "context",
			err:  agent.NewAgentError(agent.ErrorClassContext, errors.New("context exceeded")),
			code: "CONTEXT_LIMIT",
		},
		{
			name: "citation",
			err:  agent.NewAgentError(agent.ErrorClassCitation, errors.New("citation failed")),
			code: "CITATION_FAILED",
		},
		{
			name: "cancelled",
			err:  agent.NewAgentError(agent.ErrorClassCancelled, errors.New("cancelled")),
			code: "CHAT_CANCELLED",
		},
		{
			name: "convergence",
			err:  agent.NewAgentError(agent.ErrorClassConvergence, errors.New("too many rounds")),
			code: "AGENT_CONVERGENCE",
		},
		{
			name: "internal",
			err:  agent.NewAgentError(agent.ErrorClassInternal, errors.New("internal")),
			code: "CHAT_FAILED",
		},
		{
			name: "unknown",
			err:  errors.New("unknown"),
			code: "CHAT_FAILED",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			code, _ := chatErrorResponse(tt.err)
			if code != tt.code {
				t.Fatalf("code = %q, want %q", code, tt.code)
			}
		})
	}
}
