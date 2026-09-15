package agent

import "fmt"

type ErrorClass string

const (
	ErrorClassModel       ErrorClass = "model"
	ErrorClassTool        ErrorClass = "tool"
	ErrorClassRetrieval   ErrorClass = "retrieval"
	ErrorClassContext     ErrorClass = "context"
	ErrorClassCitation    ErrorClass = "citation"
	ErrorClassCancelled   ErrorClass = "cancelled"
	ErrorClassConvergence ErrorClass = "convergence"
	ErrorClassInternal    ErrorClass = "internal"
)

type AgentError struct {
	Class ErrorClass
	Err   error
}

func (e *AgentError) Error() string {
	if e == nil {
		return ""
	}
	if e.Err == nil {
		return string(e.Class)
	}
	return fmt.Sprintf("%s: %v", e.Class, e.Err)
}

func (e *AgentError) Unwrap() error {
	if e == nil {
		return nil
	}
	return e.Err
}

func NewAgentError(class ErrorClass, err error) error {
	if err == nil {
		return nil
	}
	return &AgentError{
		Class: class,
		Err:   err,
	}
}

func ErrorClassOf(err error) ErrorClass {
	if err == nil {
		return ""
	}
	if agentErr, ok := err.(*AgentError); ok {
		return agentErr.Class
	}
	return ErrorClassInternal
}
