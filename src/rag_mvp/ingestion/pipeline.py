"""Single-task document parsing, chunking, embedding, and indexing pipeline."""

from __future__ import annotations

from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.domain.ids import chunk_id, content_sha256, es_record_id
from rag_mvp.domain.models import Chunk
from rag_mvp.ingestion.checkpoints import Checkpoint, Failpoint
from rag_mvp.ports.chunker import Chunker
from rag_mvp.ports.metadata import TaskClaim
from rag_mvp.ports.model import ModelGateway
from rag_mvp.ports.parser import Parser
from rag_mvp.ports.search_engine import IndexedChunk, SearchEngine
from rag_mvp.ports.storage import ObjectStorage


class IngestionPipeline:
    def __init__(
        self,
        storage: ObjectStorage,
        parser: Parser,
        chunker: Chunker,
        model: ModelGateway,
        search: SearchEngine,
        *,
        failpoint: Failpoint | None = None,
    ) -> None:
        self._storage = storage
        self._parser = parser
        self._chunker = chunker
        self._model = model
        self._search = search
        self._failpoint = failpoint

    async def execute(self, claim: TaskClaim) -> tuple[Chunk, ...]:
        document = claim.document
        if document is None:
            raise RuntimeError("ingestion claim is missing its document")
        object_key = document.object_key
        if object_key is None:
            raise DomainError(
                DomainFailure(
                    code="OBJECT_NOT_READY",
                    message="document object is not ready for ingestion",
                    retryable=True,
                )
            )

        source = await self._storage.read(object_key)
        segments = await self._parser.parse(document.source_name, source)
        await self._checkpoint(Checkpoint.AFTER_PARSE)
        drafts = await self._chunker.split(segments)
        if not drafts:
            raise DomainError(
                DomainFailure(
                    code="EMPTY_DOCUMENT",
                    message="document produced no indexable chunks",
                    retryable=False,
                )
            )

        vectors = await self._model.embed([draft.content_with_weight for draft in drafts])
        if len(vectors) != len(drafts):
            raise DomainError(
                DomainFailure(
                    code="EMBEDDING_COUNT_MISMATCH",
                    message="embedding result count does not match chunk count",
                    retryable=True,
                )
            )

        chunks = tuple(
            Chunk(
                id=chunk_id(draft.content_with_weight, document.id),
                document_id=document.id,
                index_version=claim.job.index_version,
                ordinal=draft.ordinal,
                content_with_weight=draft.content_with_weight,
                content_sha256=content_sha256(draft.content_with_weight),
                source_name=document.source_name,
                locator=draft.locator,
                metadata=draft.metadata,
            )
            for draft in drafts
        )
        indexed = tuple(
            IndexedChunk(
                record_id=es_record_id(chunk.document_id, chunk.index_version, chunk.id),
                dataset_id=document.dataset_id,
                chunk=chunk,
                vector=vector,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        )
        await self._search.upsert_chunks(indexed)
        await self._checkpoint(Checkpoint.AFTER_INDEX_WRITE)
        return chunks

    async def _checkpoint(self, checkpoint: Checkpoint) -> None:
        if self._failpoint is not None:
            await self._failpoint(checkpoint)
