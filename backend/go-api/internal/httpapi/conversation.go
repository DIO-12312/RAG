package httpapi

import (
	"context"
	"database/sql"
	"errors"
)

// errConversationTaken 表示客户端提供的会话 id 已存在但不属于当前用户。
// 调用方必须把它映射为 404 而不是 5xx：这既避免把主键冲突误报成服务故障，
// 也不让响应差异变成跨租户的存在性探针。
var errConversationTaken = errors.New("conversation belongs to another user")

// runKey 把并发锁限定在「用户 + 会话」维度。只用会话 id 会让不同用户使用同一 id 时
// 互相阻塞（客户端可以构造任意 id），因此必须带上用户维度。
func runKey(user, conversation string) string {
	return user + ":" + conversation
}

// ensureConversation 保证会话行存在且属于当前用户。
//
// 客户端提供的会话 id 可能已经存在（同用户重复提交、多标签并发，或属于其他用户），
// 所以这里用 ON DUPLICATE KEY UPDATE 让插入幂等，再按 (id, user_id) 读回：读不到说明该
// id 被别人占用。此前的 SELECT-then-INSERT 在并发首条消息时会因主键冲突返回 503，
// 且在他人 id 上同样返回 503。
func (s *Server) ensureConversation(ctx context.Context, user, conversationID, datasetID, title string) error {
	if _, err := s.Store.DB.ExecContext(
		ctx,
		"INSERT INTO conversations(id,user_id,dataset_id,title) VALUES(?,?,?,?) ON DUPLICATE KEY UPDATE id=id",
		conversationID,
		user,
		datasetID,
		title,
	); err != nil {
		return err
	}
	var dataset string
	if err := s.Store.DB.QueryRowContext(
		ctx,
		"SELECT dataset_id FROM conversations WHERE id=? AND user_id=?",
		conversationID,
		user,
	).Scan(&dataset); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return errConversationTaken
		}
		return err
	}
	if dataset != datasetID {
		// 会话只保存最近一次选择，不能把知识库当作不可变外键。这样已删除知识库
		// 的历史会话仍可切换到一个可用知识库继续进行。
		if _, err := s.Store.DB.ExecContext(
			ctx,
			"UPDATE conversations SET dataset_id=? WHERE id=? AND user_id=?",
			datasetID,
			conversationID,
			user,
		); err != nil {
			return err
		}
	}
	return nil
}

// emptyKnowledgeBaseAnswer 是空知识库（没有任何文档）时的固定回答：它必须明确说明
// 原因与下一步动作，且不产生任何引用，避免用户以为模型“查不到”是检索质量问题。
const emptyKnowledgeBaseAnswer = "这个知识库还没有文档，暂时无法回答。请先上传资料并等待索引完成后再提问。"
