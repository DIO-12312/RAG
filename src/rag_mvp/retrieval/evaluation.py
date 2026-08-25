"""Deterministic offline retrieval quality metrics."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    relevant_chunk_ids: tuple[str, ...]
    retrieved_chunk_ids: tuple[str, ...]
    locator_matches: bool

    def __post_init__(self) -> None:
        if not self.relevant_chunk_ids:
            raise ValueError("evaluation case requires relevant chunk IDs")


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    recall_at_k: float
    mrr_at_k: float
    locator_accuracy: float


def evaluate_rankings(cases: tuple[EvaluationCase, ...], *, k: int) -> RetrievalMetrics:
    if not cases:
        raise ValueError("evaluation requires at least one case")
    if k < 1:
        raise ValueError("k must be at least 1")

    recall_total = 0.0
    reciprocal_rank_total = 0.0
    locator_total = 0
    for case in cases:
        relevant = set(case.relevant_chunk_ids)
        retrieved = case.retrieved_chunk_ids[:k]
        recall_total += len(relevant.intersection(retrieved)) / len(relevant)
        reciprocal_rank_total += next(
            (1 / rank for rank, chunk_id in enumerate(retrieved, start=1) if chunk_id in relevant),
            0.0,
        )
        locator_total += int(case.locator_matches)

    count = len(cases)
    return RetrievalMetrics(
        recall_at_k=recall_total / count,
        mrr_at_k=reciprocal_rank_total / count,
        locator_accuracy=locator_total / count,
    )
