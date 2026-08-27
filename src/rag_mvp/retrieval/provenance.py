"""将搜索候选纯转换为可追溯 evidence；最终引用编号由未来 Go 层生成。"""

from __future__ import annotations

from rag_mvp.domain.models import Evidence, ScoreBreakdown
from rag_mvp.ports.search_engine import SearchCandidate
from rag_mvp.retrieval.hybrid import HybridCandidate
from rag_mvp.retrieval.rerank import RerankedCandidate


# 执行稠密检索该方法负责的领域数据或基础设施状态。
def dense_evidence(candidate: SearchCandidate) -> Evidence:
    chunk = candidate.chunk
    return Evidence(
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        content_with_weight=chunk.content_with_weight,
        source_name=chunk.source_name,
        locator=chunk.locator,
        metadata=chunk.metadata,
        scores=ScoreBreakdown(dense_score=candidate.score),
        index_version=chunk.index_version,
    )


# 实现 hybrid_evidence 对应的局部职责。
def hybrid_evidence(candidate: HybridCandidate) -> Evidence:
    chunk = candidate.chunk
    return Evidence(
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        content_with_weight=chunk.content_with_weight,
        source_name=chunk.source_name,
        locator=chunk.locator,
        metadata=chunk.metadata,
        scores=ScoreBreakdown(
            dense_score=candidate.dense_score,
            sparse_score=candidate.sparse_score,
            fusion_score=candidate.fusion_score,
        ),
        index_version=chunk.index_version,
    )


# 实现 reranked_evidence 对应的局部职责。
def reranked_evidence(candidate: RerankedCandidate) -> Evidence:
    chunk = candidate.chunk
    return Evidence(
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        content_with_weight=chunk.content_with_weight,
        source_name=chunk.source_name,
        locator=chunk.locator,
        metadata=chunk.metadata,
        scores=ScoreBreakdown(
            dense_score=candidate.dense_score,
            sparse_score=candidate.sparse_score,
            fusion_score=candidate.fusion_score,
            rerank_score=candidate.rerank_score,
        ),
        index_version=chunk.index_version,
    )
