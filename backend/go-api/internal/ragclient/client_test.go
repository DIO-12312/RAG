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
