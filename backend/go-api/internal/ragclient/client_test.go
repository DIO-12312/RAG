package ragclient

import (
	"context"
	"testing"

	"google.golang.org/grpc"
	pb "rag-mvp/backend/go-api/internal/ragpb"
)

type deleteDatasetRPC struct {
	pb.RagServiceClient
	request *pb.DeleteDatasetRequest
	result  *pb.DeleteDatasetResponse
}

type retrieveRPC struct {
	pb.RagServiceClient
	result *pb.RetrieveResponse
}

type reindexDocumentRPC struct {
	pb.RagServiceClient
	request *pb.ReindexDocumentRequest
	result  *pb.ReindexDocumentResponse
}

func (f *reindexDocumentRPC) ReindexDocument(_ context.Context, request *pb.ReindexDocumentRequest, _ ...grpc.CallOption) (*pb.ReindexDocumentResponse, error) {
	f.request = request
	return f.result, nil
}

func (f *retrieveRPC) Retrieve(_ context.Context, _ *pb.RetrieveRequest, _ ...grpc.CallOption) (*pb.RetrieveResponse, error) {
	return f.result, nil
}

func (f *deleteDatasetRPC) DeleteDataset(_ context.Context, request *pb.DeleteDatasetRequest, _ ...grpc.CallOption) (*pb.DeleteDatasetResponse, error) {
	f.request = request
	return f.result, nil
}

func TestDeleteDatasetForwardsIdempotentCommand(t *testing.T) {
	rpc := &deleteDatasetRPC{result: &pb.DeleteDatasetResponse{Outcome: &pb.DeleteDatasetResponse_Result{Result: &pb.DeleteDatasetResult{DatasetId: "dataset-1", JobId: "job-1"}}}}
	client := &Client{RPC: rpc}

	result, err := client.DeleteDataset(context.Background(), "dataset-1", "user-delete-key")

	if err != nil {
		t.Fatal(err)
	}
	if result.GetJobId() != "job-1" || rpc.request.GetDatasetId() != "dataset-1" {
		t.Fatal("delete result or dataset scope was not forwarded")
	}
	if rpc.request.GetContext().GetIdempotencyKey() != "user-delete-key" || rpc.request.GetContext().GetRequestId() == "" {
		t.Fatal("request context was not forwarded")
	}
}

func TestDeleteDatasetRejectsBusinessErrorAndMissingResult(t *testing.T) {
	for name, response := range map[string]*pb.DeleteDatasetResponse{
		"business error": {Outcome: &pb.DeleteDatasetResponse_Error{Error: &pb.BusinessError{Code: "DATASET_NOT_FOUND", Message: "missing"}}},
		"missing result": {},
	} {
		t.Run(name, func(t *testing.T) {
			client := &Client{RPC: &deleteDatasetRPC{result: response}}
			if _, err := client.DeleteDataset(context.Background(), "dataset-1", "delete-key"); err == nil {
				t.Fatal("invalid response accepted")
			}
		})
	}
}

func TestReindexDocumentForwardsIdempotentCommand(t *testing.T) {
	rpc := &reindexDocumentRPC{result: &pb.ReindexDocumentResponse{
		Outcome: &pb.ReindexDocumentResponse_Result{Result: &pb.JobResult{
			JobId: "job-2", DocumentId: "document-1", Status: pb.JobStatus_JOB_STATUS_PENDING,
		}},
	}}
	client := &Client{RPC: rpc}

	result, err := client.ReindexDocument(context.Background(), "document-1", "user-reindex-key")

	if err != nil {
		t.Fatal(err)
	}
	if result.GetJobId() != "job-2" || rpc.request.GetDocumentId() != "document-1" {
		t.Fatal("reindex result or document scope was not forwarded")
	}
	if rpc.request.GetContext().GetIdempotencyKey() != "user-reindex-key" || rpc.request.GetContext().GetRequestId() == "" {
		t.Fatal("request context was not forwarded")
	}
}

func TestReindexDocumentRejectsBusinessErrorAndMissingResult(t *testing.T) {
	for name, response := range map[string]*pb.ReindexDocumentResponse{
		"business error": {Outcome: &pb.ReindexDocumentResponse_Error{Error: &pb.BusinessError{Code: "DOCUMENT_NOT_INDEXED", Message: "not ready"}}},
		"missing result": {},
	} {
		t.Run(name, func(t *testing.T) {
			client := &Client{RPC: &reindexDocumentRPC{result: response}}
			if _, err := client.ReindexDocument(context.Background(), "document-1", "reindex-key"); err == nil {
				t.Fatal("invalid response accepted")
			}
		})
	}
}

func TestRetrieveDisplaysPrintedAndPhysicalPDFPages(t *testing.T) {
	physicalPage := uint32(282)
	startLine := uint32(23)
	endLine := uint32(25)
	client := &Client{RPC: &retrieveRPC{result: &pb.RetrieveResponse{
		Outcome: &pb.RetrieveResponse_Result{Result: &pb.RetrieveResult{Evidence: []*pb.Evidence{{
			ChunkId: "chunk-1", DocumentId: "document-1", IndexVersion: 1,
			ContentWithWeight: "content", SourceName: "manual.pdf",
			Locator:  &pb.Locator{PageNumber: &physicalPage, StartLine: &startLine, EndLine: &endLine, Metadata: map[string]string{"printed_page_number": "276"}},
			Metadata: map[string]string{"source_type": "pdf"}, Scores: &pb.ScoreBreakdown{},
		}}}},
	}}}

	hits, err := client.Retrieve(context.Background(), "dataset-1", "query", 6)
	if err != nil {
		t.Fatal(err)
	}
	if len(hits) != 1 || hits[0].Locator != "文档第 276 页（PDF 第 282 页） · L23–L25" {
		t.Fatalf("unexpected public locator: %#v", hits)
	}
	if hits[0].Metadata["printed_page_number"] != "276" {
		t.Fatal("printed page number was not preserved in evidence metadata")
	}
	if hits[0].Metadata["physical_page_number"] != "282" {
		t.Fatal("physical page number was not preserved in evidence metadata")
	}
}

func TestRetrieveFallsBackToPhysicalPDFPageWithoutPrintedFooter(t *testing.T) {
	physicalPage := uint32(282)
	client := &Client{RPC: &retrieveRPC{result: &pb.RetrieveResponse{
		Outcome: &pb.RetrieveResponse_Result{Result: &pb.RetrieveResult{Evidence: []*pb.Evidence{{
			ChunkId: "chunk-1", DocumentId: "document-1", IndexVersion: 1,
			ContentWithWeight: "content", SourceName: "manual.pdf",
			Locator:  &pb.Locator{PageNumber: &physicalPage},
			Metadata: map[string]string{"source_type": "pdf"}, Scores: &pb.ScoreBreakdown{},
		}}}},
	}}}

	hits, err := client.Retrieve(context.Background(), "dataset-1", "query", 6)
	if err != nil {
		t.Fatal(err)
	}
	if len(hits) != 1 || hits[0].Locator != "PDF 第 282 页" {
		t.Fatalf("unexpected physical PDF locator fallback: %#v", hits)
	}
}
