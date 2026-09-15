package httpapi

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"github.com/gin-gonic/gin"
	"log/slog"
	"net/http"
	"path/filepath"
	"rag-mvp/backend/go-api/internal/ragclient"
	pb "rag-mvp/backend/go-api/internal/ragpb"
	"rag-mvp/backend/go-api/internal/storage"
	"strconv"
	"strings"
	"time"
)

// uploadReason 截断上传失败原因，避免把供应商或框架的长响应写入日志。
func uploadReason(err error) string {
	if err == nil {
		return "empty result"
	}
	reason := strings.Join(strings.Fields(err.Error()), " ")
	if len(reason) > 200 {
		reason = reason[:200] + "…"
	}
	return reason
}

// uploadFailure 把上传失败映射为 HTTP 语义：超过上限（客户端流式上限或 RAG 侧
// UPLOAD_TOO_LARGE）返回 413，其余返回 502。上限在三处独立存在（Go 客户端流式上限、
// RAG_MAX_UPLOAD_BYTES、边缘请求体上限），这里保证用户看到的语义一致。
func uploadFailure(err error) (int, string, string) {
	var business *ragclient.BusinessError
	// 服务端在流中途拒绝超限上传时，客户端看到的是流中断的 gRPC 错误，
	// 因此除了类型化错误与业务错误码，再按错误文本兜底识别稳定码。
	// 入口中间件的 MaxBytesReader 在读取 multipart 时就会失败（生产实测
	// 「http: request body too large」被映射成 502），同样属于超限。
	var tooLarge *http.MaxBytesError
	if errors.Is(err, ragclient.ErrUploadTooLarge) ||
		errors.As(err, &tooLarge) ||
		(errors.As(err, &business) && business.Code == "UPLOAD_TOO_LARGE") ||
		(err != nil && strings.Contains(err.Error(), "UPLOAD_TOO_LARGE")) ||
		(err != nil && strings.Contains(err.Error(), "request body too large")) {
		return 413, "UPLOAD_TOO_LARGE", "文件超过服务端大小上限，请压缩或拆分后重试。"
	}
	return 502, "UPLOAD_FAILED", "上传失败，请保留请求键重试。"
}

// jobFailureMessages 把 RAG 侧的稳定错误码映射为面向用户的中文说明；
// 未收录的码回退到原始 message，避免出现无法解释的空文案。
var jobFailureMessages = map[string]string{
	"EMBEDDING_AUTH_FAILED":        "Embedding 模型鉴权失败：请在设置中更新 API Key 后重试",
	"EMBEDDING_UNAVAILABLE":        "Embedding 服务不可用，请检查模型地址与网络",
	"EMBEDDING_DIMENSION_MISMATCH": "Embedding 维度与该知识库不一致，请使用相同维度的模型",
	"EMPTY_DOCUMENT":               "文档没有可索引的文本内容",
	"UPLOAD_TOO_LARGE":             "文件超过服务端大小上限",
	"PDF_OCR_UNAVAILABLE":          "扫描件需要 OCR，但服务端未启用 OCR",
	"INVALID_PDF":                  "文件不是可解析的 PDF",
}

func jobFailureMessage(j *pb.JobResult) string {
	failure := j.GetFailure()
	if failure == nil {
		return ""
	}
	if mapped, ok := jobFailureMessages[failure.GetCode()]; ok {
		return mapped
	}
	return failure.GetMessage()
}

func jobDTO(j *pb.JobResult, name string) gin.H {
	status := strings.TrimPrefix(j.Status.String(), "JOB_STATUS_")
	return gin.H{"id": j.JobId, "datasetId": j.DatasetId, "sourceName": name, "status": status, "progress": j.Progress * 100, "retryable": j.Retryable, "errorMessage": jobFailureMessage(j), "cancelRequested": j.CancelRequested, "type": strings.TrimPrefix(j.Type.String(), "JOB_TYPE_")}
}

// documentState 把 RAG 任务状态映射为产品文档状态。任务在 RAG 服务中查不到（例如 RAG
// 元数据被重置）时返回 stale，而不是让整个知识库列表返回 502：单条陈旧记录不应让用户
// 完全看不到自己的知识库。stale 文档按失败展示，可由用户在界面上删除后重新上传。
func documentState(j *pb.JobResult, err error) (state string, stale bool) {
	if err != nil || j == nil {
		return "FAILED", true
	}
	switch j.Status {
	case pb.JobStatus_JOB_STATUS_SUCCEEDED:
		return "INDEXED", false
	case pb.JobStatus_JOB_STATUS_PENDING, pb.JobStatus_JOB_STATUS_RUNNING:
		return "PROCESSING", false
	default:
		return "FAILED", false
	}
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
		state, stale := documentState(j, e)
		if state == "INDEXED" {
			ready++
		} else if state == "PROCESSING" {
			processing++
		}
		doc := gin.H{"id": d.ID, "name": d.Name, "status": state, "jobId": d.JobID}
		if stale {
			doc["stale"] = true
		}
		out = append(out, doc)
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

// ragNotFound 判断 RAG 业务错误是否表示对象在 RAG 侧已经不存在。只有明确的
// NOT_FOUND 才允许产品侧清理引用：传输错误必须继续按失败处理，避免在 RAG 仍有数据时
// 丢掉产品库引用。
func ragNotFound(e *pb.BusinessError) bool {
	return e != nil && strings.HasSuffix(e.GetCode(), "_NOT_FOUND")
}

func (s *Server) deleteDataset(c *gin.Context) {
	r, e := s.Store.Resource(c.Request.Context(), uid(c), c.Param("id"))
	if e != nil || (r.Kind != "dataset" && r.Kind != "deleting_dataset") {
		fail(c, 404, "NOT_FOUND", "资源不存在。")
		return
	}
	if r.Kind == "deleting_dataset" {
		c.JSON(202, gin.H{"datasetId": r.ID, "jobId": r.JobID})
		return
	}
	ctx, cancel := context.WithTimeout(c.Request.Context(), 30*time.Second)
	defer cancel()
	resp, e := s.RAG.RPC.DeleteDataset(ctx, &pb.DeleteDatasetRequest{Context: ragclient.Context(uid(c) + "-" + key(c)), DatasetId: r.ID})
	if e != nil {
		fail(c, 502, "DELETE_FAILED", "知识库删除失败，请稍后重试。")
		return
	}
	if ragNotFound(resp.GetError()) {
		// RAG 侧已无此知识库（元数据可能已被重置）：仍需清理产品侧引用，
		// 否则用户既删不掉也重建不了。
		if _, e = s.Store.DB.ExecContext(
			ctx,
			"UPDATE resource_index SET kind='deleted' WHERE user_id=? AND (id=? OR dataset_id=?)",
			uid(c),
			r.ID,
			r.ID,
		); e != nil {
			fail(c, 503, "SAVE_FAILED", "请重试。")
			return
		}
		c.JSON(202, gin.H{"datasetId": r.ID, "jobId": ""})
		return
	}
	if ragclient.Error(resp.GetError()) != nil || resp.GetResult() == nil {
		fail(c, 502, "DELETE_FAILED", "知识库删除失败，请稍后重试。")
		return
	}
	result := resp.GetResult()
	if _, e = s.Store.DB.ExecContext(
		ctx,
		"UPDATE resource_index SET kind=CASE WHEN id=? THEN 'deleting_dataset' ELSE 'deleted' END, job_id=CASE WHEN id=? THEN ? ELSE job_id END WHERE user_id=? AND (id=? OR dataset_id=?)",
		r.ID,
		r.ID,
		result.JobId,
		uid(c),
		r.ID,
		r.ID,
	); e != nil {
		fail(c, 503, "SAVE_FAILED", "删除任务已受理，请刷新知识库列表。")
		return
	}
	c.JSON(202, gin.H{"datasetId": result.DatasetId, "jobId": result.JobId})
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
	// 空文件没有任何可索引内容：在建立 Job/Document 之前就拒绝，
	// 避免用户拿到一个注定 FAILED(EMPTY_DOCUMENT) 的文档。
	buffered := bufio.NewReader(part)
	if _, e = buffered.Peek(1); e != nil {
		fail(c, 400, "EMPTY_FILE", "文件内容为空，请重新选择。")
		return
	}
	name := filepath.Base(part.FileName())
	ext := strings.ToLower(filepath.Ext(name))
	if !strings.Contains("|.pdf|.pptx|.md|.txt|.py|.go|.js|.ts|.java|.chm|.chi|", "|"+ext+"|") {
		fail(c, 400, "UNSUPPORTED_FILE", "暂不支持此文件格式。")
		return
	}
	ctx, cancel := context.WithTimeout(c.Request.Context(), 3*time.Minute)
	defer cancel()
	result, e := s.RAG.Upload(ctx, r.ID, name, uid(c)+"-"+key(c), buffered)
	if e != nil || result == nil {
		status, code, message := uploadFailure(e)
		// 上传失败必须留下可诊断的原因：此前 502 只有一句用户文案，无法判断是
		// 服务端超限、流中断还是连接失败。只记录截断后的错误文本，不含正文与凭据。
		slog.Info("upload_failed", "status", status, "code", code, "dataset_id", r.ID, "source_name", name, "reason", uploadReason(e))
		fail(c, status, code, message)
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
		if e != nil || j == nil {
			// 与文档状态同样降级：任务在 RAG 侧不存在时标记为 stale，而不是让整页任务列表失败。
			out = append(out, gin.H{"id": row.ID, "datasetId": r.ID, "sourceName": row.Name, "status": "FAILED", "progress": 100, "retryable": false, "errorMessage": "任务在 RAG 服务中不存在，元数据可能已被重置。", "cancelRequested": false, "type": "INGEST_DOCUMENT", "stale": true})
			continue
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
		// Retry 重新进入摄取链路前同步最新 embedding profile，避免 Dataset
		// 使用首次绑定时的旧密钥。
		if !s.bindEmbedding(c, r.DatasetID) {
			return
		}
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

func (s *Server) reindexDocument(c *gin.Context) {
	r, ok := s.owned(c, c.Param("id"), "document")
	if !ok {
		return
	}
	// Reindex 会复用 Dataset 的加密 embedding profile。先同步当前个人设置，确保
	// 用户刚替换的 API Key 不会被旧快照继续使用。
	if !s.bindEmbedding(c, r.DatasetID) {
		return
	}
	ctx, cancel := context.WithTimeout(c.Request.Context(), 30*time.Second)
	defer cancel()
	j, e := s.RAG.ReindexDocument(ctx, r.ID, uid(c)+"-"+key(c))
	if e != nil || j == nil {
		fail(c, 409, "REINDEX_FAILED", "此文档暂时无法重新索引。")
		return
	}
	job := storage.Resource{
		ID:        j.JobId,
		UserID:    uid(c),
		DatasetID: r.DatasetID,
		Kind:      "job",
		Name:      r.Name,
		JobID:     j.JobId,
	}
	if e = s.Store.Put(ctx, job); e == nil {
		_, e = s.Store.DB.ExecContext(
			ctx,
			"UPDATE resource_index SET job_id=? WHERE id=? AND user_id=? AND kind='document'",
			j.JobId,
			r.ID,
			uid(c),
		)
	}
	if e != nil {
		fail(c, 503, "SAVE_FAILED", "重新索引任务已受理，请刷新知识库列表。")
		return
	}
	c.JSON(202, jobDTO(j, r.Name))
}

func (s *Server) deleteDocument(c *gin.Context) {
	r, ok := s.owned(c, c.Param("id"), "document")
	if !ok {
		return
	}
	ctx, cancel := context.WithTimeout(c.Request.Context(), 30*time.Second)
	defer cancel()
	resp, e := s.RAG.RPC.DeleteDocument(ctx, &pb.DeleteDocumentRequest{Context: ragclient.Context(uid(c) + "-" + key(c)), DocumentId: r.ID})
	if e != nil {
		fail(c, 502, "DELETE_FAILED", "文档删除失败。")
		return
	}
	if ragNotFound(resp.GetError()) {
		// RAG 侧已无此文档：仍清理产品侧的文档与其任务引用，让用户能自行移除陈旧记录。
		if _, e = s.Store.DB.ExecContext(ctx, "UPDATE resource_index SET kind='deleted' WHERE user_id=? AND (id=? OR (kind='job' AND id=?))", uid(c), r.ID, r.JobID); e != nil {
			fail(c, 503, "SAVE_FAILED", "请用相同请求键重试。")
			return
		}
		c.Status(204)
		return
	}
	if ragclient.Error(resp.GetError()) != nil || resp.GetResult() == nil {
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

func (s *Server) sourceTopic(c *gin.Context) {
	r, ok := s.owned(c, c.Param("id"), "document")
	if !ok {
		return
	}
	topicPath := strings.TrimSpace(c.Query("topicPath"))
	version, err := strconv.ParseUint(c.Query("indexVersion"), 10, 64)
	if topicPath == "" || err != nil || version < 1 {
		fail(c, 400, "INVALID_SOURCE", "来源定位信息不完整。")
		return
	}
	result, err := s.RAG.SourceTopic(c.Request.Context(), r.ID, version, topicPath, strings.TrimSpace(c.Query("anchor")))
	if err != nil {
		fail(c, 502, "SOURCE_UNAVAILABLE", "完整来源暂时无法读取。")
		return
	}
	c.JSON(200, result)
}
