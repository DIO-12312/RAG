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
			return nil, fmt.Errorf("evidence budget exceeded: %d", p.limits.MaxEvidence)
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
	body, err := json.Marshal(citations)
	if err != nil {
		return nil, errors.New("tool result encode failed")
	}
	if len(body) > p.limits.MaxToolOutputBytes {
		return nil, fmt.Errorf("tool output budget exceeded: %d > %d", len(body), p.limits.MaxToolOutputBytes)
	}
	return body, nil
}
