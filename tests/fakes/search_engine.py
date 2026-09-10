"""仅测试使用的、支持版本过滤的确定性搜索端口。"""

from __future__ import annotations

import math
from collections.abc import Sequence

from rag_mvp.ports.search_engine import (
    IndexedChunk,
    SearchCandidate,
    SearchRequest,
    SectionContextRequest,
    TopicNeighborRequest,
    TopicReferenceRequest,
)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """计算确定性余弦相似度，供内存检索替身排序。"""
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
        """初始化测试替身的内存状态。"""
        self.records: dict[str, IndexedChunk] = {}
        self.upsert_calls = 0

    @property
    def record_count(self) -> int:
        """返回当前内存索引中的记录数。"""
        return len(self.records)

    async def upsert_chunks(self, chunks: Sequence[IndexedChunk]) -> None:
        """按物理记录标识幂等写入测试检索索引。"""
        self.upsert_calls += 1
        for indexed in chunks:
            self.records[indexed.record_id] = indexed

    async def delete_document_version(self, document_id: str, version: int) -> None:
        """删除某个文档指定索引版本的全部记录。"""
        self.records = {
            key: value
            for key, value in self.records.items()
            if not (value.chunk.document_id == document_id and value.chunk.index_version == version)
        }

    async def delete_document(self, document_id: str) -> None:
        """删除某个文档的全部索引版本。"""
        self.records = {
            key: value
            for key, value in self.records.items()
            if value.chunk.document_id != document_id
        }

    async def delete_dataset(self, dataset_id: str) -> None:
        """删除某个知识库的全部索引记录。"""
        self.records = {
            key: value for key, value in self.records.items() if value.dataset_id != dataset_id
        }

    def _matches(self, indexed: IndexedChunk, request: SearchRequest) -> bool:
        """判断记录是否满足数据集、元数据和版本过滤条件。"""
        return indexed.dataset_id == request.dataset_id and all(
            indexed.chunk.metadata.get(key) == value for key, value in request.filters.items()
        )

    async def dense_search(self, request: SearchRequest) -> Sequence[SearchCandidate]:
        """执行确定性稠密向量候选召回。"""
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
            and indexed.chunk.metadata.get("chunk_role") != "section_parent"
        ]
        # 用 record_id 作为稳定次级排序键，消除同分候选的不确定性。
        return tuple(
            sorted(candidates, key=lambda item: (-item.score, item.record_id))[: request.top_k]
        )

    async def sparse_search(self, request: SearchRequest) -> Sequence[SearchCandidate]:
        """执行确定性词项重叠候选召回。"""
        if request.query is None:
            raise ValueError("sparse search requires query")
        terms = {term.casefold() for term in request.query.split() if term}
        candidates: list[SearchCandidate] = []
        for indexed in self.records.values():
            if not self._matches(indexed, request):
                continue
            if indexed.chunk.metadata.get("chunk_role") == "section_parent":
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
        # 稀疏召回同样保持确定的排序，方便精确断言融合结果。
        return tuple(
            sorted(candidates, key=lambda item: (-item.score, item.record_id))[: request.top_k]
        )

    async def section_context(self, request: SectionContextRequest) -> Sequence[SearchCandidate]:
        """Return bounded section children and extractive parent chunks for each anchor."""

        filter_request = SearchRequest(request.dataset_id, 1, filters=request.filters)
        candidates: list[SearchCandidate] = []
        for indexed in self.records.values():
            if not self._matches(indexed, filter_request):
                continue
            chunk = indexed.chunk
            for anchor in request.anchors:
                if (
                    chunk.document_id != anchor.document_id
                    or chunk.index_version != anchor.index_version
                    or chunk.metadata.get("topic_path") != anchor.topic_path
                ):
                    continue
                role = chunk.metadata.get("chunk_role")
                sibling = (
                    role == "section_child"
                    and chunk.metadata.get("section_id") == anchor.section_id
                    and abs(
                        int(chunk.metadata.get("chunk_index_in_section", "-1000000"))
                        - anchor.chunk_index_in_section
                    )
                    <= request.sibling_radius
                )
                parent = chunk.id == anchor.parent_chunk_id
                ancestor = (
                    bool(anchor.parent_section_id)
                    and role == "section_parent"
                    and chunk.metadata.get("section_id") == anchor.parent_section_id
                )
                if sibling or parent or ancestor:
                    candidates.append(
                        SearchCandidate(indexed.record_id, indexed.dataset_id, chunk, 0.0)
                    )
                    break
        return tuple(
            sorted(
                candidates,
                key=lambda item: (
                    item.chunk.document_id,
                    item.chunk.index_version,
                    item.chunk.ordinal,
                    item.record_id,
                ),
            )
        )

    async def topic_neighbors(self, request: TopicNeighborRequest) -> Sequence[SearchCandidate]:
        """返回同文档、版本和 CHM Topic 内的确定性相邻 Chunk。"""

        candidates: list[SearchCandidate] = []
        filter_request = SearchRequest(request.dataset_id, 1, filters=request.filters)
        for indexed in self.records.values():
            if not self._matches(indexed, filter_request):
                continue
            chunk = indexed.chunk
            if not any(
                chunk.document_id == anchor.document_id
                and chunk.index_version == anchor.index_version
                and chunk.metadata.get("topic_path") == anchor.topic_path
                and abs(chunk.ordinal - anchor.ordinal) <= request.radius
                for anchor in request.anchors
            ):
                continue
            candidates.append(
                SearchCandidate(
                    record_id=indexed.record_id,
                    dataset_id=indexed.dataset_id,
                    chunk=chunk,
                    score=0.0,
                )
            )
        return tuple(
            sorted(
                candidates,
                key=lambda item: (
                    item.chunk.document_id,
                    item.chunk.index_version,
                    item.chunk.ordinal,
                    item.record_id,
                ),
            )
        )

    async def topic_references(self, request: TopicReferenceRequest) -> Sequence[SearchCandidate]:
        """Resolve CHI references to matching CHM records in deterministic score order."""

        filter_request = SearchRequest(request.dataset_id, 1, filters=request.filters)
        candidates: list[SearchCandidate] = []
        query = request.query.casefold()
        for indexed in self.records.values():
            if not self._matches(indexed, filter_request):
                continue
            chunk = indexed.chunk
            matching_anchors = [
                anchor
                for anchor in request.anchors
                if chunk.source_name == anchor.associated_source_name
                and chunk.metadata.get("source_type") == "chm"
                and chunk.metadata.get("topic_path") == anchor.topic_path
                and chunk.metadata.get("chunk_role") != "section_parent"
            ]
            if not matching_anchors:
                continue
            anchor_match = any(
                anchor.anchor and chunk.locator.metadata.get("anchor") == anchor.anchor
                for anchor in matching_anchors
            )
            score = float(anchor_match) * 10.0 + float(
                query in chunk.content_with_weight.casefold()
            )
            candidates.append(
                SearchCandidate(
                    record_id=indexed.record_id,
                    dataset_id=indexed.dataset_id,
                    chunk=chunk,
                    score=score,
                )
            )
        return tuple(
            sorted(
                candidates,
                key=lambda item: (-item.score, item.chunk.ordinal, item.record_id),
            )[: min(len(request.anchors) * 12, 100)]
        )
