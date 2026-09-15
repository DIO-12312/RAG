package httpapi

import (
	"errors"
	"testing"

	pb "rag-mvp/backend/go-api/internal/ragpb"
)

func TestDocumentStateDegradesStaleJobs(t *testing.T) {
	cases := []struct {
		name      string
		job       *pb.JobResult
		err       error
		wantState string
		wantStale bool
	}{
		{"missing job in rag", nil, errors.New("JOB_NOT_FOUND"), "FAILED", true},
		{"nil job without error", nil, nil, "FAILED", true},
		{"succeeded", &pb.JobResult{Status: pb.JobStatus_JOB_STATUS_SUCCEEDED}, nil, "INDEXED", false},
		{"running", &pb.JobResult{Status: pb.JobStatus_JOB_STATUS_RUNNING}, nil, "PROCESSING", false},
		{"pending", &pb.JobResult{Status: pb.JobStatus_JOB_STATUS_PENDING}, nil, "PROCESSING", false},
		{"failed", &pb.JobResult{Status: pb.JobStatus_JOB_STATUS_FAILED}, nil, "FAILED", false},
	}
	for _, tc := range cases {
		state, stale := documentState(tc.job, tc.err)
		if state != tc.wantState || stale != tc.wantStale {
			t.Fatalf("%s: got (%s, %v), want (%s, %v)", tc.name, state, stale, tc.wantState, tc.wantStale)
		}
	}
}

func TestRagNotFoundOnlyMatchesExplicitNotFound(t *testing.T) {
	if !ragNotFound(&pb.BusinessError{Code: "DOCUMENT_NOT_FOUND"}) {
		t.Fatal("DOCUMENT_NOT_FOUND must be treated as stale reference")
	}
	if !ragNotFound(&pb.BusinessError{Code: "DATASET_NOT_FOUND"}) {
		t.Fatal("DATASET_NOT_FOUND must be treated as stale reference")
	}
	if ragNotFound(&pb.BusinessError{Code: "DOCUMENT_DELETED"}) {
		t.Fatal("other business errors must keep failing the request")
	}
	if ragNotFound(nil) {
		t.Fatal("nil business error is not a stale reference")
	}
}

func TestJobFailureMessageMapsStableCodes(t *testing.T) {
	if got := jobFailureMessage(&pb.JobResult{Failure: &pb.JobFailure{Code: "EMBEDDING_AUTH_FAILED", Message: "embedding provider authentication failed"}}); got == "" || got == "embedding provider authentication failed" {
		t.Fatalf("已知错误码必须映射为中文说明，实际 %q", got)
	}
	if got := jobFailureMessage(&pb.JobResult{Failure: &pb.JobFailure{Code: "EMPTY_DOCUMENT", Message: "document produced no indexable chunks"}}); got == "document produced no indexable chunks" {
		t.Fatalf("EMPTY_DOCUMENT 必须映射为中文说明，实际 %q", got)
	}
	if got := jobFailureMessage(&pb.JobResult{Failure: &pb.JobFailure{Code: "SOMETHING_NEW", Message: "provider said no"}}); got != "provider said no" {
		t.Fatalf("未收录的码必须回退原始 message，实际 %q", got)
	}
	if got := jobFailureMessage(&pb.JobResult{}); got != "" {
		t.Fatalf("无失败信息时必须为空，实际 %q", got)
	}
}
