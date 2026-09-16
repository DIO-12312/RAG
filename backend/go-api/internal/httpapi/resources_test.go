package httpapi

import (
	"errors"
	"net/http"
	"testing"

	"rag-mvp/backend/go-api/internal/ragclient"

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

func TestUploadFailureMapsLimitsTo413(t *testing.T) {
	if status, code, _ := uploadFailure(ragclient.ErrUploadTooLarge); status != 413 || code != "UPLOAD_TOO_LARGE" {
		t.Fatalf("客户端流式上限必须映射为 413，实际 %d %s", status, code)
	}
	if status, code, _ := uploadFailure(&ragclient.BusinessError{Code: "UPLOAD_TOO_LARGE", Message: "upload exceeds configured byte limit"}); status != 413 || code != "UPLOAD_TOO_LARGE" {
		t.Fatalf("RAG 侧超限必须映射为 413，实际 %d %s", status, code)
	}
	if status, code, _ := uploadFailure(errors.New("rpc error: code = Unknown desc = UPLOAD_TOO_LARGE: upload exceeds configured byte limit")); status != 413 || code != "UPLOAD_TOO_LARGE" {
		t.Fatalf("流中途拒绝必须按错误码兜底映射为 413，实际 %d %s", status, code)
	}
	if status, code, _ := uploadFailure(&http.MaxBytesError{Limit: 33 << 20}); status != 413 || code != "UPLOAD_TOO_LARGE" {
		t.Fatalf("入口请求体超限必须映射为 413，实际 %d %s", status, code)
	}
	if status, code, _ := uploadFailure(errors.New("http: request body too large")); status != 413 || code != "UPLOAD_TOO_LARGE" {
		t.Fatalf("入口超限错误文本必须兜底映射为 413，实际 %d %s", status, code)
	}
	if status, code, _ := uploadFailure(&ragclient.BusinessError{Code: "DATASET_NOT_FOUND"}); status != 502 || code != "UPLOAD_FAILED" {
		t.Fatalf("其它业务错误仍为 502，实际 %d %s", status, code)
	}
	if status, _, _ := uploadFailure(errors.New("boom")); status != 502 {
		t.Fatalf("未知错误仍为 502，实际 %d", status)
	}
}

func TestUploadLimitsShareOneSource(t *testing.T) {
	t.Setenv("PRODUCT_MAX_UPLOAD_BYTES", "")
	file := ragclient.MaxUploadBytes()
	if file != 64<<20 {
		t.Fatalf("默认单文件上限应为 64 MiB，实际 %d", file)
	}
	// 入口请求体上限必须严格大于文件上限，为 multipart 边界留余量；
	// 否则会出现「界面允许 64 MB、入口按更小值拒绝」的体验缺陷。
	if body := bodyLimitBytes(); body <= file {
		t.Fatalf("请求体上限必须大于文件上限：body=%d file=%d", body, file)
	}
	t.Setenv("PRODUCT_MAX_UPLOAD_BYTES", "1048576")
	if got := ragclient.MaxUploadBytes(); got != 1048576 {
		t.Fatalf("环境变量必须覆盖默认上限，实际 %d", got)
	}
	if body := bodyLimitBytes(); body != 1048576+(1<<20) {
		t.Fatalf("请求体上限应随文件上限变化，实际 %d", body)
	}
}
