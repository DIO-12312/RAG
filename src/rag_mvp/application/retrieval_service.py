"""Dense retrieval orchestration with authoritative metadata visibility checks."""

from __future__ import annotations

from rag_mvp.application.dto import RetrieveQuery
from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.ports.metadata import MetadataRepository
from rag_mvp.ports.model import ModelGateway
from rag_mvp.ports.search_engine import SearchEngine, SearchRequest
from rag_mvp.retrieval.context_builder import ContextPlan, build_context_plan
from rag_mvp.retrieval.provenance import dense_evidence


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
        if not query.query.strip():
            raise DomainError(DomainFailure("QUERY_REQUIRED", "retrieval query is required"))
        if query.top_k < 1 or query.top_k > 100:
            raise DomainError(DomainFailure("INVALID_TOP_K", "top_k must be between 1 and 100"))
        if query.max_context_tokens < 1:
            raise DomainError(
                DomainFailure("INVALID_CONTEXT_BUDGET", "context token budget must be positive")
            )
        if query.enable_rerank:
            raise DomainError(
                DomainFailure(
                    "FEATURE_NOT_AVAILABLE",
                    "reranking is not available in the current milestone",
                )
            )

        dataset = await self._metadata.get_dataset(query.dataset_id)
        if dataset is None:
            raise DomainError(DomainFailure("DATASET_NOT_FOUND", "dataset does not exist"))
        vectors = await self._model.embed([query.query])
        if len(vectors) != 1 or len(vectors[0]) != dataset.embedding_dimension:
            raise DomainError(
                DomainFailure(
                    "EMBEDDING_DIMENSION_MISMATCH",
                    "query embedding does not match dataset dimension",
                    retryable=True,
                )
            )

        candidates = await self._search.dense_search(
            SearchRequest(
                dataset_id=query.dataset_id,
                top_k=query.top_k,
                query_vector=vectors[0],
                filters=query.filters,
            )
        )
        visible_versions = await self._metadata.visible_document_versions(
            tuple(dict.fromkeys(candidate.chunk.document_id for candidate in candidates))
        )
        visible = [
            candidate
            for candidate in candidates
            if candidate.dataset_id == query.dataset_id
            and visible_versions.get(candidate.chunk.document_id) == candidate.chunk.index_version
        ]
        visible.sort(key=lambda item: (-item.score, item.record_id))
        evidence = tuple(dense_evidence(candidate) for candidate in visible[: query.top_k])
        return build_context_plan(evidence, max_context_tokens=query.max_context_tokens)
