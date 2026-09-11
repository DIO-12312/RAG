package ragclient

import (
	"context"
	"fmt"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"io"
	"rag-mvp/backend/go-api/internal/agent"
	pb "rag-mvp/backend/go-api/internal/ragpb"
	"rag-mvp/backend/go-api/internal/security"
	"strings"
	"time"
)

type Client struct {
	RPC  pb.RagServiceClient
	Conn *grpc.ClientConn
}

type SourceTopic struct {
	DocumentID string `json:"documentId"`
	SourceName string `json:"sourceName"`
	TopicPath  string `json:"topicPath"`
	TopicTitle string `json:"topicTitle"`
	Markdown   string `json:"markdown"`
	Anchor     string `json:"anchor,omitempty"`
}

func New(target string) (*Client, error) {
	c, e := grpc.NewClient(target, grpc.WithTransportCredentials(insecure.NewCredentials()), grpc.WithDefaultCallOptions(grpc.MaxCallRecvMsgSize(4<<20)))
	if e != nil {
		return nil, e
	}
	return &Client{pb.NewRagServiceClient(c), c}, nil
}
func Context(key string) *pb.RequestContext {
	return &pb.RequestContext{RequestId: security.ID(), IdempotencyKey: key}
}
func Error(e *pb.BusinessError) error {
	if e == nil {
		return nil
	}
	return fmt.Errorf("RAG %s: %s", e.Code, e.Message)
}
func (c *Client) Create(ctx context.Context, name, model string, dim uint32, key string, profiles ...string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	profile := ""
	if len(profiles) > 0 {
		profile = profiles[0]
	}
	r, e := c.RPC.CreateDataset(ctx, &pb.CreateDatasetRequest{Context: Context(key), Name: name, EmbeddingModel: model, EmbeddingDimension: dim, EncryptedEmbeddingProfile: profile, RetrievalConfig: &pb.RetrievalConfig{DenseTopK: 20, SparseTopK: 20, RrfK: 60, MaxContextTokens: 6000}})
	if e != nil {
		return "", e
	}
	if e = Error(r.GetError()); e != nil {
		return "", e
	}
	if r.GetResult() == nil {
		return "", fmt.Errorf("missing create result")
	}
	return r.GetResult().DatasetId, nil
}

func (c *Client) DeleteDataset(ctx context.Context, dataset, key string) (*pb.DeleteDatasetResult, error) {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	r, e := c.RPC.DeleteDataset(ctx, &pb.DeleteDatasetRequest{Context: Context(key), DatasetId: dataset})
	if e != nil {
		return nil, e
	}
	if e = Error(r.GetError()); e != nil {
		return nil, e
	}
	if r.GetResult() == nil {
		return nil, fmt.Errorf("missing delete dataset result")
	}
	return r.GetResult(), nil
}

func (c *Client) Upload(ctx context.Context, dataset, name, key string, file io.Reader) (*pb.SubmitDocumentResult, error) {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	stream, e := c.RPC.SubmitDocument(ctx)
	if e != nil {
		return nil, e
	}
	if e = stream.Send(&pb.UploadDocumentRequest{Payload: &pb.UploadDocumentRequest_Header{Header: &pb.UploadHeader{Context: Context(key), DatasetId: dataset, SourceName: name}}}); e != nil {
		return nil, e
	}
	buf := make([]byte, 64*1024)
	var total int
	for {
		n, err := file.Read(buf)
		if n > 0 {
			total += n
			if total > 32*1024*1024 {
				return nil, fmt.Errorf("upload exceeds 32 MB")
			}
			if e = stream.Send(&pb.UploadDocumentRequest{Payload: &pb.UploadDocumentRequest_Data{Data: buf[:n]}}); e != nil {
				return nil, e
			}
		}
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, err
		}
	}
	r, e := stream.CloseAndRecv()
	if e != nil {
		return nil, e
	}
	return r.GetResult(), Error(r.GetError())
}
func (c *Client) Job(ctx context.Context, id string) (*pb.JobResult, error) {
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	r, e := c.RPC.GetJob(ctx, &pb.GetJobRequest{RequestId: security.ID(), JobId: id})
	if e != nil {
		return nil, e
	}
	if r.GetResult() == nil && r.GetError() == nil {
		return nil, fmt.Errorf("missing job result")
	}
	return r.GetResult(), Error(r.GetError())
}
func (c *Client) Retrieve(ctx context.Context, dataset, query string, k int) ([]agent.Evidence, error) {
	return c.retrieve(ctx, dataset, query, k, "", 60*time.Second)
}

type rerankRetriever struct {
	client  *Client
	profile string
	topN    int
	timeout time.Duration
}

func (c *Client) WithRerank(profile string, topN, timeoutSeconds int) agent.Retriever {
	return &rerankRetriever{client: c, profile: profile, topN: topN, timeout: time.Duration(timeoutSeconds+60) * time.Second}
}

func (r *rerankRetriever) Retrieve(ctx context.Context, dataset, query string, _ int) ([]agent.Evidence, error) {
	return r.client.retrieve(ctx, dataset, query, r.topN, r.profile, r.timeout)
}

func (c *Client) retrieve(ctx context.Context, dataset, query string, k int, profile string, timeout time.Duration) ([]agent.Evidence, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	r, e := c.RPC.Retrieve(ctx, &pb.RetrieveRequest{RequestId: security.ID(), DatasetId: dataset, Query: query, TopK: uint32(k), MaxContextTokens: 6000, EnableRerank: profile != "", EncryptedRerankProfile: profile})
	if e != nil {
		return nil, e
	}
	if e = Error(r.GetError()); e != nil {
		return nil, e
	}
	hits := []agent.Evidence{}
	for _, h := range r.GetResult().GetEvidence() {
		loc := []string{}
		l := h.GetLocator()
		if l.GetPageNumber() > 0 {
			loc = append(loc, fmt.Sprintf("第 %d 页", l.GetPageNumber()))
		}
		if l.GetStartLine() > 0 {
			loc = append(loc, fmt.Sprintf("L%d–L%d", l.GetStartLine(), l.GetEndLine()))
		}
		scores := map[string]float64{"fusionScore": h.GetScores().GetFusionScore()}
		if h.GetScores() != nil && h.GetScores().RerankScore != nil {
			scores["rerankScore"] = h.GetScores().GetRerankScore()
		}
		content := h.GetDisplayContent()
		if content == "" {
			content = h.GetContentWithWeight()
		}
		hits = append(hits, agent.Evidence{ChunkID: h.ChunkId, DocumentID: h.DocumentId, IndexVersion: h.IndexVersion, Content: content, SourceName: h.SourceName, Locator: strings.Join(loc, " · "), Metadata: h.Metadata, Scores: scores})
	}
	return hits, nil
}

func (c *Client) SourceTopic(ctx context.Context, document string, version uint64, topicPath, anchor string) (*SourceTopic, error) {
	ctx, cancel := context.WithTimeout(ctx, 45*time.Second)
	defer cancel()
	request := &pb.GetSourceTopicRequest{RequestId: security.ID(), DocumentId: document, IndexVersion: version, TopicPath: topicPath}
	if anchor != "" {
		request.Anchor = &anchor
	}
	response, err := c.RPC.GetSourceTopic(ctx, request)
	if err != nil {
		return nil, err
	}
	if err = Error(response.GetError()); err != nil {
		return nil, err
	}
	result := response.GetResult()
	if result == nil {
		return nil, fmt.Errorf("missing source Topic result")
	}
	return &SourceTopic{DocumentID: result.DocumentId, SourceName: result.SourceName, TopicPath: result.TopicPath, TopicTitle: result.TopicTitle, Markdown: result.Markdown, Anchor: result.GetAnchor()}, nil
}
