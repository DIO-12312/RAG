"""Pure Dense/BM25 reciprocal-rank fusion and stable ordering."""

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


def reciprocal_rank_fusion(
    dense: Sequence[SearchCandidate],
    sparse: Sequence[SearchCandidate],
    *,
    rrf_k: int,
) -> tuple[HybridCandidate, ...]:
    if rrf_k < 1:
        raise ValueError("rrf_k must be at least 1")

    candidates: dict[str, SearchCandidate] = {}
    dense_scores: dict[str, float] = {}
    sparse_scores: dict[str, float] = {}
    fusion_scores: dict[str, float] = {}
    _add_route(dense, rrf_k, candidates, dense_scores, fusion_scores)
    _add_route(sparse, rrf_k, candidates, sparse_scores, fusion_scores)

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


def _add_route(
    route: Sequence[SearchCandidate],
    rrf_k: int,
    candidates: dict[str, SearchCandidate],
    route_scores: dict[str, float],
    fusion_scores: dict[str, float],
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
        fusion_scores[candidate.record_id] = fusion_scores.get(candidate.record_id, 0.0) + 1 / (
            rrf_k + rank
        )
