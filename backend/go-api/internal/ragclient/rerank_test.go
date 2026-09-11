package ragclient

import (
	"context"
	"google.golang.org/grpc"
	pb "rag-mvp/backend/go-api/internal/ragpb"
	"testing"
)

type rerankRPC struct {
	pb.RagServiceClient
	request *pb.RetrieveRequest
}

func (f *rerankRPC) Retrieve(_ context.Context, r *pb.RetrieveRequest, _ ...grpc.CallOption) (*pb.RetrieveResponse, error) {
	f.request = r
	score := 0.9
	return &pb.RetrieveResponse{Outcome: &pb.RetrieveResponse_Result{Result: &pb.RetrieveResult{Evidence: []*pb.Evidence{{ChunkId: "best", Scores: &pb.ScoreBreakdown{RerankScore: &score}}}}}}, nil
}
func TestRequestScopedRerankDoesNotMutateSharedClient(t *testing.T) {
	rpc := &rerankRPC{}
	client := &Client{RPC: rpc}
	ranked := client.WithRerank("encrypted-profile", 3, 90)
	hits, err := ranked.Retrieve(context.Background(), "dataset", "question", 6)
	if err != nil || len(hits) != 1 || hits[0].Scores["rerankScore"] != 0.9 {
		t.Fatalf("missing rerank scores: %v %v", hits, err)
	}
	if !rpc.request.EnableRerank || rpc.request.EncryptedRerankProfile != "encrypted-profile" || rpc.request.TopK != 3 {
		t.Fatal("rerank configuration not forwarded")
	}
	_, err = client.Retrieve(context.Background(), "other", "question", 6)
	if err != nil || rpc.request.EnableRerank || rpc.request.EncryptedRerankProfile != "" || rpc.request.TopK != 6 {
		t.Fatal("rerank leaked to shared client")
	}
}
