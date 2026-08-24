"""Pure rerank score application and deterministic ordering."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rag_mvp.domain.models import Chunk
from rag_mvp.retrieval.hybrid import HybridCandidate


@dataclass(frozen=True, slots=True)
class RerankedCandidate:
    record_id: str
    dataset_id: str
    chunk: Chunk
    dense_score: float | None
    sparse_score: float | None
    fusion_score: float
    rerank_score: float


def apply_rerank_scores(
    candidates: Sequence[HybridCandidate],
    scores: Sequence[float],
    *,
    top_n: int,
) -> tuple[RerankedCandidate, ...]:
    if top_n < 1:
        raise ValueError("top_n must be at least 1")
    if len(candidates) != len(scores):
        raise ValueError("rerank score count must match candidate count")

    result = [
        RerankedCandidate(
            record_id=candidate.record_id,
            dataset_id=candidate.dataset_id,
            chunk=candidate.chunk,
            dense_score=candidate.dense_score,
            sparse_score=candidate.sparse_score,
            fusion_score=candidate.fusion_score,
            rerank_score=score,
        )
        for candidate, score in zip(candidates, scores, strict=True)
    ]
    return tuple(
        sorted(
            result,
            key=lambda item: (-item.rerank_score, -item.fusion_score, item.record_id),
        )[:top_n]
    )
