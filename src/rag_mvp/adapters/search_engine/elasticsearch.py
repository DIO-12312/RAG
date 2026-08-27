"""Elasticsearch 版本化 Dense KNN 与 BM25 检索实现；不负责应用层融合。"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from elasticsearch import ApiError, AsyncElasticsearch, TransportError
from elasticsearch.helpers import async_bulk

from rag_mvp.adapters.search_engine.mapping import (
    bulk_action,
    candidate_from_hit,
    index_definition,
)
from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.ports.search_engine import IndexedChunk, SearchCandidate, SearchRequest

FILTER_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


class ElasticsearchSearchEngine:
    """Persist versioned chunks and expose separate dense and BM25 candidate routes."""

    # 初始化该对象的依赖、配置或受控资源。
    def __init__(
        self,
        client: AsyncElasticsearch,
        index_name: str,
        embedding_dimension: int,
    ) -> None:
        if not index_name.strip():
            raise ValueError("index_name must not be empty")
        if embedding_dimension < 1:
            raise ValueError("embedding_dimension must be at least 1")
        self._client = client
        self._index_name = index_name
        self._embedding_dimension = embedding_dimension

    @property
    # 实现 index_name 对应的局部职责。
    def index_name(self) -> str:
        return self._index_name

    # 幂等确保该方法负责的领域数据或基础设施状态。
    async def ensure_index(self) -> None:
        """Create the index if absent and reject incompatible existing mappings."""

        definition = index_definition(self._embedding_dimension)
        try:
            exists = bool(await self._client.indices.exists(index=self._index_name))
            if not exists:
                try:
                    await self._client.indices.create(
                        index=self._index_name,
                        mappings=definition["mappings"],
                    )
                except ApiError:
                    if not bool(await self._client.indices.exists(index=self._index_name)):
                        raise
            response = await self._client.indices.get_mapping(index=self._index_name)
        except (ApiError, TransportError) as exc:
            raise self._unavailable("search index could not be provisioned") from exc

        raw_mapping = response.get(self._index_name, {}).get("mappings", {})
        if not isinstance(raw_mapping, Mapping) or not self._mapping_matches(raw_mapping):
            raise DomainError(
                DomainFailure(
                    "SEARCH_SCHEMA_MISMATCH",
                    "Elasticsearch index mapping does not match the configured schema",
                    retryable=False,
                )
            )

    # 按资源所有权顺序关闭底层连接或句柄。
    async def close(self) -> None:
        await self._client.close()

    # 实现 upsert_chunks 对应的局部职责。
    async def upsert_chunks(self, chunks: Sequence[IndexedChunk]) -> None:
        if not chunks:
            return
        actions = [
            bulk_action(self._index_name, chunk, self._embedding_dimension) for chunk in chunks
        ]
        try:
            succeeded, failed = await async_bulk(
                self._client,
                actions,
                stats_only=True,
                raise_on_error=False,
                raise_on_exception=False,
                refresh="wait_for",
            )
        except (ApiError, TransportError) as exc:
            raise self._unavailable("chunks could not be indexed") from exc
        if succeeded != len(actions) or failed:
            raise self._unavailable("one or more chunks could not be indexed")

    # 删除该方法负责的领域数据或基础设施状态。
    async def delete_document_version(self, document_id: str, version: int) -> None:
        if not document_id.strip():
            raise ValueError("document_id must not be empty")
        if version < 1:
            raise ValueError("version must be at least 1")
        await self._delete_by_filters(
            [
                {"term": {"document_id": document_id}},
                {"term": {"index_version": version}},
            ]
        )

    # 删除该方法负责的领域数据或基础设施状态。
    async def delete_document(self, document_id: str) -> None:
        if not document_id.strip():
            raise ValueError("document_id must not be empty")
        await self._delete_by_filters([{"term": {"document_id": document_id}}])

    # 删除该方法负责的领域数据或基础设施状态。
    async def delete_dataset(self, dataset_id: str) -> None:
        if not dataset_id.strip():
            raise ValueError("dataset_id must not be empty")
        await self._delete_by_filters([{"term": {"dataset_id": dataset_id}}])

    # 执行稠密检索该方法负责的领域数据或基础设施状态。
    async def dense_search(self, request: SearchRequest) -> Sequence[SearchCandidate]:
        if request.query_vector is None:
            raise ValueError("dense search requires query_vector")
        if len(request.query_vector) != self._embedding_dimension:
            raise ValueError("query vector dimension must match the Elasticsearch mapping")
        filters = self._request_filters(request)
        try:
            response = await self._client.search(
                index=self._index_name,
                knn={
                    "field": "vector",
                    "query_vector": list(request.query_vector),
                    "k": request.top_k,
                    "num_candidates": max(request.top_k * 10, 100),
                    "filter": {"bool": {"filter": filters}},
                },
                size=request.top_k,
                sort=[{"_score": {"order": "desc"}}, {"record_id": {"order": "asc"}}],
                track_scores=True,
            )
        except (ApiError, TransportError) as exc:
            raise self._unavailable("dense search failed") from exc
        return self._candidates(cast(Mapping[str, Any], response.body))

    # 执行稀疏检索该方法负责的领域数据或基础设施状态。
    async def sparse_search(self, request: SearchRequest) -> Sequence[SearchCandidate]:
        if request.query is None or not request.query.strip():
            raise ValueError("sparse search requires a non-empty query")
        filters = self._request_filters(request)
        try:
            response = await self._client.search(
                index=self._index_name,
                query={
                    "bool": {
                        "must": [{"match": {"content_with_weight": request.query}}],
                        "filter": filters,
                    }
                },
                size=request.top_k,
                sort=[{"_score": {"order": "desc"}}, {"record_id": {"order": "asc"}}],
                track_scores=True,
            )
        except (ApiError, TransportError) as exc:
            raise self._unavailable("BM25 search failed") from exc
        return self._candidates(cast(Mapping[str, Any], response.body))

    # 内部辅助：完成 delete_by_filters 所需的局部转换或校验。
    async def _delete_by_filters(self, filters: list[dict[str, Any]]) -> None:
        try:
            await self._client.delete_by_query(
                index=self._index_name,
                query={"bool": {"filter": filters}},
                conflicts="proceed",
                refresh=True,
            )
        except (ApiError, TransportError) as exc:
            raise self._unavailable("indexed chunks could not be deleted") from exc

    @staticmethod
    # 内部辅助：完成 candidates 所需的局部转换或校验。
    def _candidates(response: Mapping[str, Any]) -> tuple[SearchCandidate, ...]:
        try:
            hits = response["hits"]["hits"]
            if not isinstance(hits, list):
                raise TypeError("hits must be a list")
            candidates = tuple(candidate_from_hit(hit) for hit in hits)
        except (KeyError, TypeError) as exc:
            raise DomainError(
                DomainFailure(
                    "SEARCH_RESPONSE_INVALID",
                    "search engine returned an invalid response",
                    retryable=False,
                )
            ) from exc
        return tuple(sorted(candidates, key=lambda item: (-item.score, item.record_id)))

    @staticmethod
    # 内部辅助：完成 request_filters 所需的局部转换或校验。
    def _request_filters(request: SearchRequest) -> list[dict[str, Any]]:
        if not request.dataset_id.strip():
            raise ValueError("dataset_id must not be empty")
        filters: list[dict[str, Any]] = [{"term": {"dataset_id": request.dataset_id}}]
        for key, value in sorted(request.filters.items()):
            if not FILTER_KEY.fullmatch(key):
                raise ValueError("metadata filter keys contain unsupported characters")
            filters.append({"term": {f"metadata.{key}": value}})
        return filters

    # 内部辅助：完成 mapping_matches 所需的局部转换或校验。
    def _mapping_matches(self, mapping: Mapping[str, Any]) -> bool:
        properties = mapping.get("properties")
        if mapping.get("dynamic") != "strict" or not isinstance(properties, Mapping):
            return False
        expected = index_definition(self._embedding_dimension)["mappings"]["properties"]
        for name, expected_field in expected.items():
            actual_field = properties.get(name)
            if not isinstance(actual_field, Mapping):
                return False
            if not self._field_mapping_matches(expected_field, actual_field):
                return False
        return True

    @classmethod
    # 内部辅助：完成 field_mapping_matches 所需的局部转换或校验。
    def _field_mapping_matches(
        cls,
        expected: Mapping[str, Any],
        actual: Mapping[str, Any],
    ) -> bool:
        expected_type = expected.get("type")
        actual_type = actual.get("type")
        if expected_type == "object" and actual_type is None:
            pass
        elif actual_type != expected_type:
            return False
        for option in ("dims", "index", "similarity", "dynamic"):
            if option in expected and actual.get(option) != expected[option]:
                return False
        for container in ("properties", "fields"):
            expected_children = expected.get(container)
            if expected_children is None:
                continue
            actual_children = actual.get(container)
            if not isinstance(expected_children, Mapping) or not isinstance(
                actual_children, Mapping
            ):
                return False
            for name, expected_child in expected_children.items():
                actual_child = actual_children.get(name)
                if not isinstance(expected_child, Mapping) or not isinstance(actual_child, Mapping):
                    return False
                if not cls._field_mapping_matches(expected_child, actual_child):
                    return False
        return True

    @staticmethod
    # 内部辅助：完成 unavailable 所需的局部转换或校验。
    def _unavailable(message: str) -> DomainError:
        return DomainError(DomainFailure("SEARCH_UNAVAILABLE", message, retryable=True))
