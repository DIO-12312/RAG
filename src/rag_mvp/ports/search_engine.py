"""Dense and sparse search capability boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from rag_mvp.domain.models import Chunk


@dataclass(frozen=True, slots=True)
class IndexedChunk:
    record_id: str
    dataset_id: str
    chunk: Chunk
    vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class SearchRequest:
    dataset_id: str
    top_k: int
    query: str | None = None
    query_vector: tuple[float, ...] | None = None
    filters: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.top_k < 1:
            raise ValueError("top_k must be at least 1")


@dataclass(frozen=True, slots=True)
class SearchCandidate:
    record_id: str
    dataset_id: str
    chunk: Chunk
    score: float


class SearchEngine(Protocol):
    """Index and retrieve versioned chunks through Elasticsearch."""

    async def upsert_chunks(self, chunks: Sequence[IndexedChunk]) -> None: ...

    async def delete_document_version(self, document_id: str, version: int) -> None: ...

    async def delete_document(self, document_id: str) -> None: ...

    async def delete_dataset(self, dataset_id: str) -> None: ...

    async def dense_search(self, request: SearchRequest) -> Sequence[SearchCandidate]: ...

    async def sparse_search(self, request: SearchRequest) -> Sequence[SearchCandidate]: ...
