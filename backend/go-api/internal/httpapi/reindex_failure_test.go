package httpapi

import (
	"errors"
	"fmt"
	"testing"

	"rag-mvp/backend/go-api/internal/ragclient"
)

// 未建立索引或源对象缺失时必须给出可执行的下一步，而不是笼统的失败提示。
func TestReindexFailureMapsActionableMessages(t *testing.T) {
	cases := []struct {
		name        string
		err         error
		wantCode    string
		wantMessage string
	}{
		{
			name:        "not indexed",
			err:         &ragclient.BusinessError{Code: "DOCUMENT_NOT_INDEXED", Message: "raw"},
			wantCode:    "DOCUMENT_NOT_INDEXED",
			wantMessage: "该文档尚未成功建立索引：请先重试失败的任务，或删除后重新上传。",
		},
		{
			name:        "object missing",
			err:         &ragclient.BusinessError{Code: "REINDEX_OBJECT_MISSING", Message: "raw"},
			wantCode:    "REINDEX_OBJECT_MISSING",
			wantMessage: "该文档的源文件已不可用，请删除后重新上传。",
		},
		{
			name:        "wrapped business error",
			err:         fmt.Errorf("reindex: %w", &ragclient.BusinessError{Code: "DOCUMENT_NOT_INDEXED"}),
			wantCode:    "DOCUMENT_NOT_INDEXED",
			wantMessage: "该文档尚未成功建立索引：请先重试失败的任务，或删除后重新上传。",
		},
		{
			name:        "transport failure",
			err:         errors.New("connection refused"),
			wantCode:    "REINDEX_FAILED",
			wantMessage: "此文档暂时无法重新索引。",
		},
		{
			name:        "unknown business code",
			err:         &ragclient.BusinessError{Code: "SOMETHING_ELSE", Message: "raw"},
			wantCode:    "REINDEX_FAILED",
			wantMessage: "此文档暂时无法重新索引。",
		},
	}

	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			status, code, message := reindexFailure(testCase.err)
			if status != 409 {
				t.Fatalf("status = %d, want 409", status)
			}
			if code != testCase.wantCode {
				t.Fatalf("code = %q, want %q", code, testCase.wantCode)
			}
			if message != testCase.wantMessage {
				t.Fatalf("message = %q, want %q", message, testCase.wantMessage)
			}
		})
	}
}
