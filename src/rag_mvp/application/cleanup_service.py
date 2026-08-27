"""Application use case for one idempotent document cleanup task."""

from __future__ import annotations

from datetime import datetime

from rag_mvp.application.ingestion_service import IngestionExecution
from rag_mvp.domain.enums import TaskType
from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.ports.metadata import MetadataRepository
from rag_mvp.ports.search_engine import SearchEngine
from rag_mvp.ports.storage import ObjectStorage


class CleanupService:
    def __init__(
        self,
        metadata: MetadataRepository,
        search: SearchEngine,
        storage: ObjectStorage,
    ) -> None:
        self._metadata = metadata
        self._search = search
        self._storage = storage

    async def execute(
        self, task_id: str, delivery_sequence: int, now: datetime
    ) -> IngestionExecution:
        claim = await self._metadata.claim_task(task_id, delivery_sequence, now)
        if claim is None:
            return IngestionExecution(claimed=False, completed=False)
        try:
            if claim.task.type is TaskType.CLEANUP_DATASET:
                await self._search.delete_dataset(claim.dataset.id)
                for object_key in await self._metadata.dataset_cleanup_object_keys(task_id):
                    await self._storage.delete(object_key)
            elif claim.task.type is TaskType.CLEANUP_INDEX_VERSION:
                if claim.document is None:
                    raise RuntimeError("document cleanup claim is missing its document")
                await self._search.delete_document_version(
                    claim.document.id, claim.job.index_version
                )
            else:
                if claim.document is None:
                    raise RuntimeError("document cleanup claim is missing its document")
                await self._search.delete_document(claim.document.id)
                if claim.document.object_key is not None:
                    await self._storage.delete(claim.document.object_key)
        except DomainError as error:
            return IngestionExecution(claimed=True, completed=False, failure=error.failure)
        except Exception as error:
            return IngestionExecution(
                claimed=True,
                completed=False,
                failure=DomainFailure(
                    "CLEANUP_RETRYABLE",
                    str(error) or type(error).__name__,
                    retryable=True,
                ),
            )
        completed = (
            await self._metadata.finalize_dataset_cleanup(task_id, now)
            if claim.task.type is TaskType.CLEANUP_DATASET
            else await self._metadata.complete_cleanup(task_id, now)
        )
        if completed:
            return IngestionExecution(claimed=True, completed=True)
        return IngestionExecution(
            claimed=True,
            completed=False,
            failure=DomainFailure(
                "CLEANUP_FENCE_MISMATCH",
                "cleanup completion was rejected by its generation fence",
            ),
        )
