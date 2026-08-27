"""Dense retrieval orchestration with authoritative metadata visibility checks."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from time import perf_counter

from rag_mvp.application.dto import RetrieveQuery
from rag_mvp.domain.enums import DatasetStatus
from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.domain.models import Evidence
from rag_mvp.observability import emit_event
from rag_mvp.ports.metadata import MetadataRepository
from rag_mvp.ports.model import ModelGateway
from rag_mvp.ports.search_engine import SearchCandidate, SearchEngine, SearchRequest
from rag_mvp.retrieval.context_builder import ContextPlan, build_context_plan
from rag_mvp.retrieval.hybrid import HybridCandidate, reciprocal_rank_fusion
from rag_mvp.retrieval.provenance import hybrid_evidence, reranked_evidence
from rag_mvp.retrieval.rerank import apply_rerank_scores


class RetrievalService:
    def __init__(
        self,
        metadata: MetadataRepository,
        search: SearchEngine,
        model: ModelGateway,
    ) -> None:
        self._metadata = metadata
        self._search = search
        self._model = model

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
        vectors = await self._model.embed([query.query])
        if len(vectors) != 1 or len(vectors[0]) != dataset.embedding_dimension:
            raise DomainError(
                DomainFailure(
                    "EMBEDDING_DIMENSION_MISMATCH",
                    "query embedding does not match dataset dimension",
                    retryable=True,
                )
            )

        candidate_limit = min(max(query.top_k * 4, 20), 100)
        dense_candidates, sparse_candidates = await asyncio.gather(
            self._search.dense_search(
                SearchRequest(
                    dataset_id=query.dataset_id,
                    top_k=candidate_limit,
                    query_vector=vectors[0],
                    filters=query.filters,
                )
            ),
            self._search.sparse_search(
                SearchRequest(
                    dataset_id=query.dataset_id,
                    top_k=candidate_limit,
                    query=query.query,
                    filters=query.filters,
                )
            ),
        )
        visible_versions = await self._metadata.visible_document_versions(
            tuple(
                dict.fromkeys(
                    candidate.chunk.document_id
                    for candidate in (*dense_candidates, *sparse_candidates)
                )
            )
        )
        visible_dense = self._visible(dense_candidates, query.dataset_id, visible_versions)
        visible_sparse = self._visible(sparse_candidates, query.dataset_id, visible_versions)
        fused = reciprocal_rank_fusion(visible_dense, visible_sparse, rrf_k=60)
        evidence = await self._evidence(query, fused)
        result = build_context_plan(evidence, max_context_tokens=query.max_context_tokens)
        emit_event(
            "retrieval_completed",
            request_id=query.request_id,
            dataset_id=query.dataset_id,
            stage="hybrid_retrieve",
            duration_ms=(perf_counter() - started_at) * 1000,
        )
        return result

    async def _evidence(
        self, query: RetrieveQuery, fused: Sequence[HybridCandidate]
    ) -> tuple[Evidence, ...]:
        if not query.enable_rerank:
            return tuple(hybrid_evidence(candidate) for candidate in fused[: query.top_k])

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

    @staticmethod
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
