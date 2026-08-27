"""Versioned deterministic search port used only by tests."""

from __future__ import annotations

import math
from collections.abc import Sequence

from rag_mvp.ports.search_engine import IndexedChunk, SearchCandidate, SearchRequest


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise ValueError("vector dimensions must match")
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denominator == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator


class FakeSearchEngine:
    def __init__(self) -> None:
        self.records: dict[str, IndexedChunk] = {}
        self.upsert_calls = 0

    @property
    def record_count(self) -> int:
        return len(self.records)

    async def upsert_chunks(self, chunks: Sequence[IndexedChunk]) -> None:
        self.upsert_calls += 1
        for indexed in chunks:
            self.records[indexed.record_id] = indexed

    async def delete_document_version(self, document_id: str, version: int) -> None:
        self.records = {
            key: value
            for key, value in self.records.items()
            if not (value.chunk.document_id == document_id and value.chunk.index_version == version)
        }

    async def delete_document(self, document_id: str) -> None:
        self.records = {
            key: value
            for key, value in self.records.items()
            if value.chunk.document_id != document_id
        }

    async def delete_dataset(self, dataset_id: str) -> None:
        self.records = {
            key: value for key, value in self.records.items() if value.dataset_id != dataset_id
        }

    def _matches(self, indexed: IndexedChunk, request: SearchRequest) -> bool:
        return indexed.dataset_id == request.dataset_id and all(
            indexed.chunk.metadata.get(key) == value for key, value in request.filters.items()
        )

    async def dense_search(self, request: SearchRequest) -> Sequence[SearchCandidate]:
        if request.query_vector is None:
            raise ValueError("dense search requires query_vector")
        candidates = [
            SearchCandidate(
                record_id=indexed.record_id,
                dataset_id=indexed.dataset_id,
                chunk=indexed.chunk,
                score=_cosine(request.query_vector, indexed.vector),
            )
            for indexed in self.records.values()
            if self._matches(indexed, request)
        ]
        return tuple(
            sorted(candidates, key=lambda item: (-item.score, item.record_id))[: request.top_k]
        )

    async def sparse_search(self, request: SearchRequest) -> Sequence[SearchCandidate]:
        if request.query is None:
            raise ValueError("sparse search requires query")
        terms = {term.casefold() for term in request.query.split() if term}
        candidates: list[SearchCandidate] = []
        for indexed in self.records.values():
            if not self._matches(indexed, request):
                continue
            words = {term.casefold() for term in indexed.chunk.content_with_weight.split()}
            overlap = len(terms & words)
            if overlap:
                candidates.append(
                    SearchCandidate(
                        record_id=indexed.record_id,
                        dataset_id=indexed.dataset_id,
                        chunk=indexed.chunk,
                        score=overlap / max(len(terms), 1),
                    )
                )
        return tuple(
            sorted(candidates, key=lambda item: (-item.score, item.record_id))[: request.top_k]
        )
