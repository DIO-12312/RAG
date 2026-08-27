from __future__ import annotations

import pytest

from rag_mvp.domain.ids import content_sha256
from rag_mvp.domain.models import Chunk, Locator
from rag_mvp.retrieval.hybrid import HybridCandidate
from rag_mvp.retrieval.rerank import apply_rerank_scores


def _candidate(record_id: str, fusion_score: float) -> HybridCandidate:
    """构造本测试所需的输入、替身或运行环境。"""
    content = f"content {record_id}"
    return HybridCandidate(
        record_id=record_id,
        dataset_id="dataset-1",
        chunk=Chunk(
            id=f"chunk-{record_id}",
            document_id=f"document-{record_id}",
            index_version=1,
            ordinal=0,
            content_with_weight=content,
            content_sha256=content_sha256(content),
            source_name="guide.txt",
            locator=Locator(start_line=1, end_line=1),
        ),
        dense_score=0.5,
        sparse_score=1.0,
        fusion_score=fusion_score,
    )


def test_rerank_scores_reorder_stably_and_keep_fusion_data() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    candidates = (_candidate("a", 0.04), _candidate("b", 0.03), _candidate("c", 0.02))

    result = apply_rerank_scores(candidates, (0.1, 0.9, 0.9), top_n=2)

    assert [item.record_id for item in result] == ["b", "c"]
    assert result[0].rerank_score == 0.9
    assert result[0].fusion_score == 0.03
    assert result[0].dense_score == 0.5


def test_rerank_rejects_score_count_mismatch() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    with pytest.raises(ValueError, match="count"):
        apply_rerank_scores((_candidate("a", 0.1),), (), top_n=1)


def test_rerank_rejects_invalid_top_n() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    with pytest.raises(ValueError, match="top_n"):
        apply_rerank_scores((), (), top_n=0)
