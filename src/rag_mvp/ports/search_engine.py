"""稠密与稀疏检索能力边界：适配器分别给出候选，融合留给 retrieval 层。"""

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

    # 在构造完成后校验并固化领域不变式。
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

    # 实现 upsert_chunks 对应的局部职责。
    async def upsert_chunks(self, chunks: Sequence[IndexedChunk]) -> None: ...

    # 删除该方法负责的领域数据或基础设施状态。
    async def delete_document_version(self, document_id: str, version: int) -> None: ...

    # 删除该方法负责的领域数据或基础设施状态。
    async def delete_document(self, document_id: str) -> None: ...

    # 删除该方法负责的领域数据或基础设施状态。
    async def delete_dataset(self, dataset_id: str) -> None: ...

    # 执行稠密检索该方法负责的领域数据或基础设施状态。
    async def dense_search(self, request: SearchRequest) -> Sequence[SearchCandidate]: ...

    # 执行稀疏检索该方法负责的领域数据或基础设施状态。
    async def sparse_search(self, request: SearchRequest) -> Sequence[SearchCandidate]: ...
