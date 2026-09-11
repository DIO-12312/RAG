"""纯 Dense/BM25 RRF 融合与稳定排序；这里是唯一的跨路由分数合并位置。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rag_mvp.domain.models import Chunk
from rag_mvp.ports.search_engine import SearchCandidate


@dataclass(frozen=True, slots=True)
class HybridCandidate:
    record_id: str
    dataset_id: str
    chunk: Chunk
    dense_score: float | None
    sparse_score: float | None
    fusion_score: float


def merge_ranked_routes(
    routes: Sequence[Sequence[SearchCandidate]],
    *,
    rrf_k: int = 60,
) -> tuple[SearchCandidate, ...]:
    """Merge multiple subquery rankings from the same retrieval route."""
    if rrf_k < 1:
        raise ValueError("rrf_k must be at least 1")

    candidates: dict[str, SearchCandidate] = {}
    rank_scores: dict[str, float] = {}
    best_ranks: dict[str, int] = {}
    raw_scores: dict[str, float] = {}
    for route in routes:
        seen: set[str] = set()
        for rank, candidate in enumerate(route, start=1):
            if candidate.record_id in seen:
                continue
            seen.add(candidate.record_id)
            existing = candidates.get(candidate.record_id)
            if existing is not None and (
                existing.dataset_id != candidate.dataset_id or existing.chunk != candidate.chunk
            ):
                raise ValueError("one record_id must identify the same chunk on every route")
            candidates[candidate.record_id] = candidate
            rank_scores[candidate.record_id] = rank_scores.get(candidate.record_id, 0.0) + 1 / (
                rrf_k + rank
            )
            best_ranks[candidate.record_id] = min(best_ranks.get(candidate.record_id, rank), rank)
            raw_scores[candidate.record_id] = max(
                raw_scores.get(candidate.record_id, candidate.score), candidate.score
            )

    ordered_ids = sorted(
        candidates,
        key=lambda record_id: (
            -rank_scores[record_id],
            best_ranks[record_id],
            record_id,
        ),
    )
    return tuple(
        SearchCandidate(
            record_id=record_id,
            dataset_id=candidates[record_id].dataset_id,
            chunk=candidates[record_id].chunk,
            score=raw_scores[record_id],
        )
        for record_id in ordered_ids
    )


# 实现 reciprocal_rank_fusion 对应的局部职责。
def reciprocal_rank_fusion(
    dense: Sequence[SearchCandidate],
    sparse: Sequence[SearchCandidate],
    *,
    rrf_k: int,
    sparse_weight: float = 1.0,
) -> tuple[HybridCandidate, ...]:
    if rrf_k < 1:
        raise ValueError("rrf_k must be at least 1")
    if sparse_weight <= 0:
        raise ValueError("sparse_weight must be positive")

    candidates: dict[str, SearchCandidate] = {}
    dense_scores: dict[str, float] = {}
    sparse_scores: dict[str, float] = {}
    fusion_scores: dict[str, float] = {}
    _add_route(dense, rrf_k, candidates, dense_scores, fusion_scores)
    _add_route(sparse, rrf_k, candidates, sparse_scores, fusion_scores, weight=sparse_weight)

    result = [
        HybridCandidate(
            record_id=record_id,
            dataset_id=candidate.dataset_id,
            chunk=candidate.chunk,
            dense_score=dense_scores.get(record_id),
            sparse_score=sparse_scores.get(record_id),
            fusion_score=fusion_scores[record_id],
        )
        for record_id, candidate in candidates.items()
    ]
    return tuple(sorted(result, key=lambda item: (-item.fusion_score, item.record_id)))


# 内部辅助：完成 add_route 所需的局部转换或校验。
def _add_route(
    route: Sequence[SearchCandidate],
    rrf_k: int,
    candidates: dict[str, SearchCandidate],
    route_scores: dict[str, float],
    fusion_scores: dict[str, float],
    weight: float = 1.0,
) -> None:
    seen: set[str] = set()
    for rank, candidate in enumerate(route, start=1):
        if candidate.record_id in seen:
            continue
        seen.add(candidate.record_id)
        existing = candidates.get(candidate.record_id)
        if existing is not None and (
            existing.dataset_id != candidate.dataset_id or existing.chunk != candidate.chunk
        ):
            raise ValueError("one record_id must identify the same chunk on every route")
        candidates[candidate.record_id] = candidate
        route_scores[candidate.record_id] = candidate.score
        fusion_scores[candidate.record_id] = fusion_scores.get(
            candidate.record_id, 0.0
        ) + weight / (rrf_k + rank)
