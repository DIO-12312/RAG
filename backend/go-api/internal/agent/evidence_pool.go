package agent

import (
	"encoding/json"
	"errors"
	"fmt"
)

// EvidencePool 跨检索轮次去重 Evidence，并只在新 Evidence 首次加入时分配 Citation 编号。
type EvidencePool struct {
	ordinals map[string]int
	items    []Citation
	limits   RunLimits
}

// NewEvidencePool 构造受 RunLimits 约束的 Evidence 池。
func NewEvidencePool(limits RunLimits) *EvidencePool {
	return &EvidencePool{ordinals: map[string]int{}, limits: limits}
}

// evidenceKey 以 document_id/index_version/chunk_id 作为唯一键，保留新旧索引版本。
func evidenceKey(hit Evidence) string {
	return fmt.Sprintf("%s/%d/%s", hit.DocumentID, hit.IndexVersion, hit.ChunkID)
}

// Add 返回本轮命中的 Citation；重复 Evidence 复用既有编号，历史编号永不改变。
func (p *EvidencePool) Add(hits []Evidence) ([]Citation, error) {
	result := make([]Citation, 0, len(hits))

	for _, hit := range hits {
		key := evidenceKey(hit)
		if ordinal, ok := p.ordinals[key]; ok {
			result = append(result, p.items[ordinal-1])
			continue
		}
		if len(p.items) >= p.limits.MaxEvidence {
			// Evidence 容量是模型上下文保护边界，不应把一次本可回答的请求
			// 变成硬失败。保留稳定的前 N 条及其 citation 编号，忽略尾部候选。
			continue
		}
		ordinal := len(p.items) + 1
		p.ordinals[key] = ordinal
		p.items = append(p.items, Citation{Ordinal: ordinal, Evidence: hit})
		result = append(result, p.items[ordinal-1])
	}

	return result, nil
}

// Citations 返回按编号排序的全部已知 Evidence 副本。
func (p *EvidencePool) Citations() []Citation {
	return append([]Citation(nil), p.items...)
}

// Len 返回池中唯一 Evidence 数量。
func (p *EvidencePool) Len() int { return len(p.items) }

// EncodeResult 序列化本轮工具结果，并执行单次工具结果大小预算。
func (p *EvidencePool) EncodeResult(citations []Citation) ([]byte, error) {
	for {
		body, err := json.Marshal(citations)
		if err != nil {
			return nil, errors.New("tool result encode failed")
		}
		if len(body) <= p.limits.MaxToolOutputBytes || len(citations) == 0 {
			return body, nil
		}
		// 尾部候选优先级最低；逐条移除直到工具结果可以安全送入模型。
		citations = citations[:len(citations)-1]
	}
}
