"""检索编排：先从 ES 召回，再按权威元数据复核可见版本与删除状态。"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping, Sequence
from time import perf_counter

from rag_mvp.application.dto import RetrieveQuery
from rag_mvp.domain.enums import DatasetStatus
from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.domain.models import Chunk, Evidence, ScoreBreakdown
from rag_mvp.observability import emit_event
from rag_mvp.ports.metadata import MetadataRepository
from rag_mvp.ports.model import ModelGateway, model_for_dataset
from rag_mvp.ports.search_engine import (
    SearchCandidate,
    SearchEngine,
    SearchRequest,
    TopicNeighborAnchor,
    TopicNeighborRequest,
    TopicReferenceAnchor,
    TopicReferenceRequest,
)
from rag_mvp.retrieval.context_builder import ContextPlan, build_context_plan
from rag_mvp.retrieval.hybrid import (
    HybridCandidate,
    merge_ranked_routes,
    reciprocal_rank_fusion,
)
from rag_mvp.retrieval.provenance import hybrid_evidence, reranked_evidence
from rag_mvp.retrieval.query_analysis import analyze_query
from rag_mvp.retrieval.rerank import apply_rerank_scores


class RetrievalService:
    # 初始化该对象的依赖、配置或受控资源。
    def __init__(
        self,
        metadata: MetadataRepository,
        search: SearchEngine,
        model: ModelGateway,
    ) -> None:
        self._metadata = metadata
        self._search = search
        self._model = model

    # ES 候选并非最终可见结果；必须以 MySQL 的 active_version/删除状态二次过滤。
    async def retrieve(self, query: RetrieveQuery) -> ContextPlan:
        started_at = perf_counter()
        if not query.query.strip():
            raise DomainError(DomainFailure("QUERY_REQUIRED", "retrieval query is required"))
        if query.top_k < 1 or query.top_k > 100:
            raise DomainError(DomainFailure("INVALID_TOP_K", "top_k must be between 1 and 100"))
        if query.max_context_tokens < 1:
            raise DomainError(
                DomainFailure("INVALID_CONTEXT_BUDGET", "context token budget must be positive")
            )
        dataset = await self._metadata.get_dataset(query.dataset_id)
        if dataset is None:
            raise DomainError(DomainFailure("DATASET_NOT_FOUND", "dataset does not exist"))
        if dataset.status is not DatasetStatus.ACTIVE:
            raise DomainError(DomainFailure("DATASET_DELETING", "dataset is being deleted"))
        analysis = analyze_query(query.query)
        search_queries = analysis.subqueries
        vectors = await model_for_dataset(self._model, dataset).embed(list(search_queries))
        if len(vectors) != len(search_queries) or any(
            len(vector) != dataset.embedding_dimension for vector in vectors
        ):
            raise DomainError(
                DomainFailure(
                    "EMBEDDING_DIMENSION_MISMATCH",
                    "query embedding does not match dataset dimension",
                    retryable=True,
                )
            )

        candidate_limit = min(max(query.top_k * 4, 20), 100)
        route_results = await asyncio.gather(
            *(
                self._search.dense_search(
                    SearchRequest(
                        dataset_id=query.dataset_id,
                        top_k=candidate_limit,
                        query_vector=vector,
                        filters=query.filters,
                    )
                )
                for vector in vectors
            ),
            *(
                self._search.sparse_search(
                    SearchRequest(
                        dataset_id=query.dataset_id,
                        top_k=candidate_limit,
                        query=search_query,
                        filters=query.filters,
                    )
                )
                for search_query in search_queries
            ),
        )
        route_count = len(search_queries)
        dense_routes = route_results[:route_count]
        sparse_routes = route_results[route_count:]
        all_candidates = tuple(candidate for route in route_results for candidate in route)
        visible_versions = await self._metadata.visible_document_versions(
            tuple(dict.fromkeys(candidate.chunk.document_id for candidate in all_candidates))
        )
        visible_dense_routes = tuple(
            self._visible(route, query.dataset_id, visible_versions) for route in dense_routes
        )
        visible_sparse_routes = tuple(
            self._visible(route, query.dataset_id, visible_versions) for route in sparse_routes
        )
        visible_dense = merge_ranked_routes(visible_dense_routes, rrf_k=60)
        visible_sparse = merge_ranked_routes(visible_sparse_routes, rrf_k=60)
        fused = reciprocal_rank_fusion(
            visible_dense,
            visible_sparse,
            rrf_k=60,
        )
        fused = self._prioritize_identifiers(analysis.normalized_query, fused)
        anchors = await self._evidence(query, fused)
        anchors, referenced_chunks = await self._expand_chi_topic_references(
            query,
            anchors,
            search_query=analysis.normalized_query,
        )
        evidence = await self._expand_topic_neighbors(
            query,
            anchors,
            (
                *(candidate.chunk for candidate in fused),
                *referenced_chunks,
            ),
        )
        result = build_context_plan(evidence, max_context_tokens=query.max_context_tokens)
        emit_event(
            "retrieval_completed",
            request_id=query.request_id,
            dataset_id=query.dataset_id,
            stage="hybrid_retrieve",
            duration_ms=(perf_counter() - started_at) * 1000,
        )
        return result

    @staticmethod
    def _prioritize_identifiers(
        query: str, candidates: Sequence[HybridCandidate]
    ) -> tuple[HybridCandidate, ...]:
        """Prefer chunks that repeat an explicit API/enum identifier in the query."""
        identifiers = tuple(
            dict.fromkeys(
                token.casefold()
                for token in re.findall(r"\b[A-Za-z][A-Za-z0-9_]{4,}\b", query)
                if "_" in token
            )
        )
        if not identifiers:
            return tuple(candidates)

        def identifier_score(candidate: HybridCandidate) -> tuple[int, int]:
            chunk = candidate.chunk
            exact_fields = (
                chunk.locator.symbol or "",
                chunk.metadata.get("topic_title", ""),
                chunk.metadata.get("topic_url", ""),
            )
            exact_matches = sum(
                field.casefold() == identifier
                for identifier in identifiers
                for field in exact_fields
            )
            searchable = "\n".join(
                (
                    chunk.content_with_weight,
                    *exact_fields,
                )
            ).casefold()
            occurrences = sum(searchable.count(identifier) for identifier in identifiers)
            return exact_matches, occurrences

        return tuple(
            sorted(
                candidates,
                key=lambda candidate: (
                    -identifier_score(candidate)[0],
                    -identifier_score(candidate)[1],
                    -candidate.fusion_score,
                    candidate.record_id,
                ),
            )
        )

    async def _expand_chi_topic_references(
        self,
        query: RetrieveQuery,
        anchors: Sequence[Evidence],
        *,
        search_query: str,
    ) -> tuple[tuple[Evidence, ...], tuple[Chunk, ...]]:
        reference_requests = tuple(
            TopicReferenceAnchor(
                anchor_chunk_id=anchor.chunk_id,
                associated_source_name=anchor.metadata["associated_chm_source_name"],
                topic_path=anchor.metadata["topic_path"],
                anchor=anchor.metadata.get("anchor"),
            )
            for anchor in anchors
            if anchor.metadata.get("source_type") == "chi"
            and anchor.metadata.get("associated_chm_source_name")
            and anchor.metadata.get("topic_path")
        )
        if not reference_requests:
            return tuple(anchors), ()

        try:
            candidates = await self._search.topic_references(
                TopicReferenceRequest(
                    dataset_id=query.dataset_id,
                    query=search_query,
                    anchors=reference_requests,
                    filters=query.filters,
                )
            )
        except DomainError as error:
            if not error.failure.retryable:
                raise
            return tuple(anchors), ()

        visible_versions = await self._metadata.visible_document_versions(
            tuple(dict.fromkeys(candidate.chunk.document_id for candidate in candidates))
        )
        visible = self._visible(candidates, query.dataset_id, visible_versions)
        by_reference: dict[str, SearchCandidate] = {}
        for reference in reference_requests:
            match = next(
                (
                    candidate
                    for candidate in visible
                    if candidate.chunk.source_name == reference.associated_source_name
                    and candidate.chunk.metadata.get("source_type") == "chm"
                    and candidate.chunk.metadata.get("topic_path") == reference.topic_path
                ),
                None,
            )
            if match is not None:
                by_reference[reference.anchor_chunk_id] = match

        referenced_chunks: list[Chunk] = []
        direct_reference_anchors: dict[tuple[str, int, str], Evidence] = {}
        anchor_keys = {
            (anchor.document_id, anchor.index_version, anchor.chunk_id)
            for anchor in anchors
            if anchor.metadata.get("source_type") != "chi"
        }
        for chi_anchor in anchors:
            candidate = by_reference.get(chi_anchor.chunk_id)
            if candidate is None:
                continue
            chunk = candidate.chunk
            key = (chunk.document_id, chunk.index_version, chunk.id)
            if key in anchor_keys:
                direct_reference_anchors.setdefault(key, chi_anchor)

        expanded: list[Evidence] = []
        emitted_references: set[tuple[str, int, str]] = set()
        for anchor in anchors:
            anchor_key = (anchor.document_id, anchor.index_version, anchor.chunk_id)
            matched_chi_anchor = direct_reference_anchors.get(anchor_key)
            if matched_chi_anchor is not None:
                # Keep the normal anchor's rank and genuine retrieval scores,
                # but record that CHI independently resolved the same Topic.
                expanded.append(self._annotate_direct_topic_reference(anchor, matched_chi_anchor))
                continue
            expanded.append(anchor)
            candidate = by_reference.get(anchor.chunk_id)
            if candidate is None:
                continue
            chunk = candidate.chunk
            key = (chunk.document_id, chunk.index_version, chunk.id)
            if key in emitted_references:
                continue
            emitted_references.add(key)
            referenced_chunks.append(chunk)
            if key in direct_reference_anchors:
                continue
            expanded.append(self._topic_reference_evidence(chunk, anchor))
        return tuple(expanded), tuple(referenced_chunks)

    # 内部辅助：完成 evidence 所需的局部转换或校验。
    async def _evidence(
        self, query: RetrieveQuery, fused: Sequence[HybridCandidate]
    ) -> tuple[Evidence, ...]:
        if not query.enable_rerank:
            selected: list[HybridCandidate] = []
            deferred: list[HybridCandidate] = []
            seen_topics: set[tuple[str, str]] = set()
            for candidate in fused:
                metadata = candidate.chunk.metadata
                # CHM chunks carry topic_path; diversify direct anchors by
                # Topic even when older indexed records lack source_type.
                if metadata.get("topic_path"):
                    topic = (candidate.chunk.document_id, metadata.get("topic_path", ""))
                    if topic in seen_topics:
                        deferred.append(candidate)
                        continue
                    seen_topics.add(topic)
                selected.append(candidate)
                if len(selected) == query.top_k:
                    break
            if len(selected) < query.top_k:
                selected.extend(deferred[: query.top_k - len(selected)])
            return tuple(hybrid_evidence(candidate) for candidate in selected)

        candidates = tuple(fused[:20])
        try:
            scores = await self._model.rerank(
                query.query,
                [candidate.chunk.content_with_weight for candidate in candidates],
            )
        except DomainError as error:
            if not error.failure.retryable:
                raise
            return tuple(hybrid_evidence(candidate) for candidate in fused[: query.top_k])
        except (ConnectionError, TimeoutError):
            return tuple(hybrid_evidence(candidate) for candidate in fused[: query.top_k])

        try:
            ranked = apply_rerank_scores(candidates, scores, top_n=query.top_k)
        except ValueError as error:
            raise DomainError(
                DomainFailure(
                    "RERANK_SCORE_MISMATCH",
                    str(error),
                    retryable=True,
                )
            ) from error
        return tuple(reranked_evidence(candidate) for candidate in ranked)

    async def _expand_topic_neighbors(
        self,
        query: RetrieveQuery,
        anchors: Sequence[Evidence],
        chunks: Sequence[Chunk],
    ) -> tuple[Evidence, ...]:
        chunk_lookup = {
            (chunk.document_id, chunk.index_version, chunk.id): chunk for chunk in chunks
        }
        eligible: list[tuple[Evidence, Chunk]] = []
        requests: list[TopicNeighborAnchor] = []
        for anchor in anchors:
            chunk = chunk_lookup.get((anchor.document_id, anchor.index_version, anchor.chunk_id))
            topic_path = anchor.metadata.get("topic_path", "")
            if chunk is None or anchor.metadata.get("source_type") != "chm" or not topic_path:
                continue
            eligible.append((anchor, chunk))
            requests.append(
                TopicNeighborAnchor(
                    document_id=chunk.document_id,
                    index_version=chunk.index_version,
                    ordinal=chunk.ordinal,
                    topic_path=topic_path,
                )
            )
        if not requests:
            return tuple(anchors)

        try:
            neighbors = await self._search.topic_neighbors(
                TopicNeighborRequest(
                    dataset_id=query.dataset_id,
                    anchors=tuple(requests),
                    radius=1,
                    filters=query.filters,
                )
            )
        except DomainError as error:
            if not error.failure.retryable:
                raise
            return tuple(anchors)

        anchor_keys = {
            (anchor.document_id, anchor.index_version, anchor.chunk_id) for anchor in anchors
        }
        seen = set(anchor_keys)
        expanded = list(anchors)
        for anchor, anchor_chunk in eligible:
            topic_path = anchor.metadata["topic_path"]
            matches = sorted(
                (
                    candidate
                    for candidate in neighbors
                    if candidate.dataset_id == query.dataset_id
                    and candidate.chunk.document_id == anchor_chunk.document_id
                    and candidate.chunk.index_version == anchor_chunk.index_version
                    and candidate.chunk.metadata.get("topic_path") == topic_path
                    and 0 < abs(candidate.chunk.ordinal - anchor_chunk.ordinal) <= 1
                ),
                key=lambda candidate: (
                    abs(candidate.chunk.ordinal - anchor_chunk.ordinal),
                    candidate.chunk.ordinal,
                    candidate.record_id,
                ),
            )
            for candidate in matches:
                key = (
                    candidate.chunk.document_id,
                    candidate.chunk.index_version,
                    candidate.chunk.id,
                )
                if key in seen:
                    continue
                seen.add(key)
                expanded.append(
                    self._topic_neighbor_evidence(
                        candidate.chunk,
                        anchor,
                        candidate.chunk.ordinal - anchor_chunk.ordinal,
                    )
                )
        return tuple(expanded)

    @staticmethod
    def _topic_reference_evidence(chunk: Chunk, anchor: Evidence) -> Evidence:
        metadata = dict(chunk.metadata)
        metadata.update(
            {
                "retrieval_role": "chi_topic_reference",
                "anchor_chunk_id": anchor.chunk_id,
                "chi_topic_url": anchor.metadata.get("topic_url", ""),
            }
        )
        return Evidence(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            content_with_weight=chunk.content_with_weight,
            source_name=chunk.source_name,
            locator=chunk.locator,
            scores=ScoreBreakdown(),
            index_version=chunk.index_version,
            metadata=metadata,
        )

    @staticmethod
    def _annotate_direct_topic_reference(evidence: Evidence, anchor: Evidence) -> Evidence:
        metadata = dict(evidence.metadata)
        metadata.update(
            {
                "chi_reference_anchor_chunk_id": anchor.chunk_id,
                "chi_topic_url": anchor.metadata.get("topic_url", ""),
            }
        )
        return Evidence(
            chunk_id=evidence.chunk_id,
            document_id=evidence.document_id,
            content_with_weight=evidence.content_with_weight,
            source_name=evidence.source_name,
            locator=evidence.locator,
            scores=evidence.scores,
            index_version=evidence.index_version,
            metadata=metadata,
        )

    @staticmethod
    def _topic_neighbor_evidence(
        chunk: Chunk,
        anchor: Evidence,
        distance: int,
    ) -> Evidence:
        metadata = dict(chunk.metadata)
        metadata.update(
            {
                "retrieval_role": "topic_neighbor",
                "anchor_chunk_id": anchor.chunk_id,
                "neighbor_distance": str(distance),
            }
        )
        return Evidence(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            content_with_weight=chunk.content_with_weight,
            source_name=chunk.source_name,
            locator=chunk.locator,
            scores=ScoreBreakdown(),
            index_version=chunk.index_version,
            metadata=metadata,
        )

    @staticmethod
    # 内部辅助：完成 visible 所需的局部转换或校验。
    def _visible(
        candidates: Sequence[SearchCandidate],
        dataset_id: str,
        visible_versions: Mapping[str, int],
    ) -> tuple[SearchCandidate, ...]:
        return tuple(
            candidate
            for candidate in candidates
            if candidate.dataset_id == dataset_id
            and visible_versions.get(candidate.chunk.document_id) == candidate.chunk.index_version
        )
