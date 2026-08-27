"""仅测试使用的、支持版本过滤的确定性搜索端口。"""

from __future__ import annotations

import math
from collections.abc import Sequence

from rag_mvp.ports.search_engine import IndexedChunk, SearchCandidate, SearchRequest


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
