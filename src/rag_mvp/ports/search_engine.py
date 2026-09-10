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
class TopicNeighborAnchor:
    document_id: str
    index_version: int
    ordinal: int
    topic_path: str

    def __post_init__(self) -> None:
        if not self.document_id.strip():
            raise ValueError("document_id must not be empty")
        if self.index_version < 1:
            raise ValueError("index_version must be at least 1")
        if self.ordinal < 0:
            raise ValueError("ordinal must not be negative")
        if not self.topic_path.strip():
            raise ValueError("topic_path must not be empty")


@dataclass(frozen=True, slots=True)
class TopicNeighborRequest:
    dataset_id: str
    anchors: tuple[TopicNeighborAnchor, ...]
    radius: int = 1
    filters: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.dataset_id.strip():
            raise ValueError("dataset_id must not be empty")
        if self.radius < 1:
            raise ValueError("radius must be at least 1")


@dataclass(frozen=True, slots=True)
class SectionContextAnchor:
    """A directly retrieved CHM child used to resolve section-local context."""

    document_id: str
    index_version: int
    chunk_id: str
    parent_chunk_id: str
    section_id: str
    parent_section_id: str
    chunk_index_in_section: int
    topic_path: str

    def __post_init__(self) -> None:
        if not self.document_id.strip() or not self.chunk_id.strip():
            raise ValueError("document_id and chunk_id must not be empty")
        if self.index_version < 1:
            raise ValueError("index_version must be at least 1")
        if not self.parent_chunk_id.strip() or not self.section_id.strip():
            raise ValueError("parent_chunk_id and section_id must not be empty")
        if self.chunk_index_in_section < 0:
            raise ValueError("chunk_index_in_section must not be negative")
        if not self.topic_path.strip():
            raise ValueError("topic_path must not be empty")


@dataclass(frozen=True, slots=True)
class SectionContextRequest:
    dataset_id: str
    anchors: tuple[SectionContextAnchor, ...]
    sibling_radius: int = 1
    filters: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.dataset_id.strip():
            raise ValueError("dataset_id must not be empty")
        if self.sibling_radius < 1:
            raise ValueError("sibling_radius must be at least 1")


@dataclass(frozen=True, slots=True)
class TopicReferenceAnchor:
    """A CHI hit that points to one Topic in its associated CHM document."""

    anchor_chunk_id: str
    associated_source_name: str
    topic_path: str
    anchor: str | None = None

    def __post_init__(self) -> None:
        if not self.anchor_chunk_id.strip():
            raise ValueError("anchor_chunk_id must not be empty")
        if not self.associated_source_name.strip():
            raise ValueError("associated_source_name must not be empty")
        if not self.topic_path.strip():
            raise ValueError("topic_path must not be empty")


@dataclass(frozen=True, slots=True)
class TopicReferenceRequest:
    dataset_id: str
    query: str
    anchors: tuple[TopicReferenceAnchor, ...]
    filters: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.dataset_id.strip():
            raise ValueError("dataset_id must not be empty")
        if not self.query.strip():
            raise ValueError("query must not be empty")


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

    # 读取 CHM 锚点同 Topic 内的相邻物理 Chunk，供应用层进行上下文扩展。
    async def topic_neighbors(self, request: TopicNeighborRequest) -> Sequence[SearchCandidate]: ...

    # 读取同标题段的子块邻居、当前标题段父块及可用的上级标题段父块。
    async def section_context(
        self, request: SectionContextRequest
    ) -> Sequence[SearchCandidate]: ...

    # 按 CHI 的 Topic 路径定位同 Dataset 内关联 CHM 的正文 Chunk。
    async def topic_references(
        self, request: TopicReferenceRequest
    ) -> Sequence[SearchCandidate]: ...
