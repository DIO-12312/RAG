from __future__ import annotations

import pytest

from rag_mvp.domain.ids import content_sha256
from rag_mvp.domain.models import Chunk, Locator
from rag_mvp.ports.search_engine import SearchCandidate
from rag_mvp.retrieval.hybrid import reciprocal_rank_fusion


def _candidate(record_id: str, score: float) -> SearchCandidate:
    """构造本测试所需的输入、替身或运行环境。"""
    chunk = Chunk(
        id=f"chunk-{record_id}",
        document_id=f"document-{record_id}",
        index_version=1,
        ordinal=0,
        content_with_weight=record_id,
        content_sha256=content_sha256(record_id),
        source_name="guide.txt",
        locator=Locator(start_line=1, end_line=1),
    )
    return SearchCandidate(record_id, "dataset-1", chunk, score)


def test_rrf_fuses_routes_deduplicates_and_keeps_stage_scores() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    dense = (_candidate("a", 0.9), _candidate("b", 0.8))
    sparse = (_candidate("b", 7.0), _candidate("c", 5.0))

    result = reciprocal_rank_fusion(dense, sparse, rrf_k=60)

    assert [item.record_id for item in result] == ["b", "a", "c"]
    assert result[0].dense_score == 0.8
    assert result[0].sparse_score == 7.0
    assert result[0].fusion_score == pytest.approx(1 / 62 + 1 / 61)
    assert result[1].sparse_score is None
    assert result[2].dense_score is None


def test_rrf_uses_record_id_as_stable_final_tie_breaker() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    result = reciprocal_rank_fusion((_candidate("b", 1.0),), (_candidate("a", 1.0),), rrf_k=60)

    assert [item.record_id for item in result] == ["a", "b"]


def test_rrf_rejects_invalid_constant() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    with pytest.raises(ValueError, match="rrf_k"):
        reciprocal_rank_fusion((), (), rrf_k=0)
