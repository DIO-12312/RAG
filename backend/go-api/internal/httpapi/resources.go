package httpapi

import (
	"context"
	"fmt"
	"github.com/gin-gonic/gin"
	"path/filepath"
	"rag-mvp/backend/go-api/internal/ragclient"
	pb "rag-mvp/backend/go-api/internal/ragpb"
	"rag-mvp/backend/go-api/internal/storage"
	"strings"
	"time"
)

func jobDTO(j *pb.JobResult, name string) gin.H {
	status := strings.TrimPrefix(j.Status.String(), "JOB_STATUS_")
	return gin.H{"id": j.JobId, "datasetId": j.DatasetId, "sourceName": name, "status": status, "progress": j.Progress * 100, "retryable": j.Retryable, "errorMessage": j.GetFailure().GetMessage(), "cancelRequested": j.CancelRequested, "type": strings.TrimPrefix(j.Type.String(), "JOB_TYPE_")}
}
func (s *Server) summary(c *gin.Context, r storage.Resource) (gin.H, error) {
	docs, e := s.Store.List(c.Request.Context(), uid(c), "document", r.ID)
	if e != nil {
		return nil, e
	}
	out := []gin.H{}
	ready := 0
	processing := 0
	for _, d := range docs {
		j, e := s.RAG.Job(c.Request.Context(), d.JobID)
		if e != nil {
			return nil, e
		}
		state := "FAILED"
		if j.Status == pb.JobStatus_JOB_STATUS_SUCCEEDED {
			state = "INDEXED"
			ready++
		} else if j.Status == pb.JobStatus_JOB_STATUS_PENDING || j.Status == pb.JobStatus_JOB_STATUS_RUNNING {
			state = "PROCESSING"
			processing++
		}
		out = append(out, gin.H{"id": d.ID, "name": d.Name, "status": state})
	}
	state := "EMPTY"
	if ready > 0 {
		state = "READY"
	} else if processing > 0 {
		state = "PROCESSING"
	} else if len(docs) > 0 {
		state = "FAILED"
	}
	return gin.H{"id": r.ID, "name": r.Name, "status": state, "documentCount": len(docs), "updatedAt": r.UpdatedAt, "documents": out}, nil
}
func (s *Server) datasets(c *gin.Context) {
	rows, e := s.Store.List(c.Request.Context(), uid(c), "dataset", "")
	if e != nil {
		fail(c, 503, "LIST_FAILED", "知识库加载失败。")
		return
	}
	out := []gin.H{}
	for _, r := range rows {
		m, e := s.summary(c, r)
		if e != nil {
			fail(c, 502, "RAG_UNAVAILABLE", "RAG 服务暂不可用。")
			return
		}
		out = append(out, m)
	}
	c.JSON(200, out)
}
func (s *Server) dataset(c *gin.Context) {
	r, ok := s.owned(c, c.Param("id"), "dataset")
	if !ok {
		return
	}
	m, e := s.summary(c, r)
	if e != nil {
		fail(c, 502, "RAG_UNAVAILABLE", "RAG 服务暂不可用。")
		return
	}
	c.JSON(200, m)
}
func (s *Server) createDataset(c *gin.Context) {
	var p struct {
		Name string `json:"name"`
	}
	if c.ShouldBindJSON(&p) != nil || len(strings.TrimSpace(p.Name)) == 0 || len(p.Name) > 200 {
		fail(c, 400, "INVALID_INPUT", "请输入 1–200 字节的知识库名称。")
		return
	}
	model, dimension, profile, e := s.embeddingSnapshot(c.Request.Context(), uid(c))
	if e != nil {
		fail(c, 409, "EMBEDDING_NOT_CONFIGURED", "请先在设置中配置 Embedding 模型。")
		return
	}
	id, e := s.RAG.Create(c.Request.Context(), p.Name, model, dimension, uid(c)+"-"+key(c), profile)
	if e != nil {
		fail(c, 502, "RAG_CREATE_FAILED", "知识库创建失败，请检查 Python 服务配置。")
		return
	}
	r := storage.Resource{ID: id, DatasetID: id, Kind: "dataset", UserID: uid(c), Name: p.Name}
	if e = s.Store.Put(c.Request.Context(), r); e != nil {
		fail(c, 503, "INDEX_SAVE_FAILED", "登记失败，请用相同请求键重试。")
		return
	}
	c.JSON(201, gin.H{"id": id, "name": p.Name, "status": "EMPTY", "documentCount": 0, "updatedAt": time.Now().UTC().Format(time.RFC3339)})
}
func (s *Server) upload(c *gin.Context) {
	r, ok := s.owned(c, c.Param("id"), "dataset")
	if !ok {
		return
	}
	if !s.bindEmbedding(c, r.ID) {
		return
	}
	reader, e := c.Request.MultipartReader()
	if e != nil {
		fail(c, 400, "INVALID_UPLOAD", "请选择文件上传。")
		return
	}
	part, e := reader.NextPart()
	if e != nil || part.FormName() != "file" || part.FileName() == "" {
		fail(c, 400, "INVALID_UPLOAD", "缺少 file 字段。")
		return
	}
	defer part.Close()
	name := filepath.Base(part.FileName())
	ext := strings.ToLower(filepath.Ext(name))
	if !strings.Contains("|.pdf|.md|.txt|.py|.go|.js|.ts|.java|.chm|.chi|", "|"+ext+"|") {
		fail(c, 400, "UNSUPPORTED_FILE", "暂不支持此文件格式。")
		return
	}
	ctx, cancel := context.WithTimeout(c.Request.Context(), 3*time.Minute)
	defer cancel()
	result, e := s.RAG.Upload(ctx, r.ID, name, uid(c)+"-"+key(c), part)
	if e != nil || result == nil {
		fail(c, 502, "UPLOAD_FAILED", "上传失败，请保留请求键重试。")
		return
	}
	doc := storage.Resource{ID: result.DocumentId, UserID: uid(c), DatasetID: r.ID, Kind: "document", Name: name, JobID: result.JobId}
	job := doc
	job.ID = result.JobId
	job.Kind = "job"
	if e = s.Store.Put(ctx, doc); e == nil {
		e = s.Store.Put(ctx, job)
	}
	if e != nil {
		fail(c, 503, "INDEX_SAVE_FAILED", "资源登记失败，请用相同请求键重试。")
		return
	}
	c.JSON(202, gin.H{"id": result.JobId, "datasetId": r.ID, "sourceName": name, "status": "PENDING", "progress": 0, "retryable": false})
}
func (s *Server) jobs(c *gin.Context) {
	r, ok := s.owned(c, c.Param("id"), "dataset")
	if !ok {
		return
	}
	rows, e := s.Store.List(c.Request.Context(), uid(c), "job", r.ID)
	if e != nil {
		fail(c, 503, "LIST_FAILED", "任务加载失败。")
		return
	}
	out := []gin.H{}
	for _, row := range rows {
		j, e := s.RAG.Job(c.Request.Context(), row.ID)
		if e != nil {
			fail(c, 502, "RAG_UNAVAILABLE", "任务查询失败。")
			return
		}
		out = append(out, jobDTO(j, row.Name))
	}
	c.JSON(200, out)
}
func (s *Server) jobAction(c *gin.Context) {
	r, ok := s.owned(c, c.Param("id"), "job")
	if !ok {
		return
	}
	ctx, cancel := context.WithTimeout(c.Request.Context(), 30*time.Second)
	defer cancel()
	switch c.Param("action") {
	case "cancel":
		resp, e := s.RAG.RPC.CancelJob(ctx, &pb.CancelJobRequest{Context: ragclient.Context(uid(c) + "-" + key(c)), JobId: r.ID})
		if e != nil || ragclient.Error(resp.GetError()) != nil {
			fail(c, 409, "CANCEL_FAILED", "此任务无法取消。")
			return
		}
	case "retry":
		resp, e := s.RAG.RPC.RetryJob(ctx, &pb.RetryJobRequest{Context: ragclient.Context(uid(c) + "-" + key(c)), JobId: r.ID})
		if e != nil || ragclient.Error(resp.GetError()) != nil || resp.GetResult() == nil {
			fail(c, 409, "RETRY_FAILED", "此任务不可重试。")
			return
		}
		j := resp.GetResult()
		r.ID = j.JobId
		r.JobID = j.JobId
		if e = s.Store.Put(ctx, r); e != nil {
			fail(c, 503, "SAVE_FAILED", "请用相同请求键重试。")
			return
		}
		_, e = s.Store.DB.ExecContext(ctx, "UPDATE resource_index SET job_id=? WHERE id=? AND user_id=? AND kind='document'", j.JobId, j.DocumentId, uid(c))
		if e != nil {
			fail(c, 503, "SAVE_FAILED", "请用相同请求键重试。")
			return
		}
		c.JSON(200, jobDTO(j, r.Name))
		return
	default:
		fail(c, 404, "NOT_FOUND", "操作不存在。")
		return
	}
	j, e := s.RAG.Job(ctx, r.ID)
	if e != nil {
		fail(c, 502, "RAG_UNAVAILABLE", "任务查询失败。")
		return
	}
	c.JSON(200, jobDTO(j, r.Name))
}
func (s *Server) deleteDocument(c *gin.Context) {
	r, ok := s.owned(c, c.Param("id"), "document")
	if !ok {
		return
	}
	ctx, cancel := context.WithTimeout(c.Request.Context(), 30*time.Second)
	defer cancel()
	resp, e := s.RAG.RPC.DeleteDocument(ctx, &pb.DeleteDocumentRequest{Context: ragclient.Context(uid(c) + "-" + key(c)), DocumentId: r.ID})
	if e != nil || ragclient.Error(resp.GetError()) != nil || resp.GetResult() == nil {
		fail(c, 502, "DELETE_FAILED", "文档删除失败。")
		return
	}
	job := storage.Resource{ID: resp.GetResult().JobId, UserID: uid(c), DatasetID: r.DatasetID, Kind: "job", Name: fmt.Sprintf("删除 %s", r.Name), JobID: resp.GetResult().JobId}
	if e = s.Store.Put(ctx, job); e == nil {
		_, e = s.Store.DB.ExecContext(ctx, "UPDATE resource_index SET kind='deleted' WHERE id=? AND user_id=?", r.ID, uid(c))
	}
	if e != nil {
		fail(c, 503, "SAVE_FAILED", "请用相同请求键重试。")
		return
	}
	c.Status(204)
}
