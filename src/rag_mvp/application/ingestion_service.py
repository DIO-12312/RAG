"""Application use case for conditionally executing one ingestion task."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.ingestion.pipeline import IngestionPipeline
from rag_mvp.ports.metadata import MetadataRepository


@dataclass(frozen=True, slots=True)
class IngestionExecution:
    claimed: bool
    completed: bool
    failure: DomainFailure | None = None


class IngestionService:
    def __init__(self, metadata: MetadataRepository, pipeline: IngestionPipeline) -> None:
        self._metadata = metadata
        self._pipeline = pipeline

    async def execute(
        self,
        task_id: str,
        delivery_sequence: int,
        now: datetime,
    ) -> IngestionExecution:
        claim = await self._metadata.claim_task(task_id, delivery_sequence, now)
        if claim is None:
            return IngestionExecution(claimed=False, completed=False)

        try:
            chunks = await self._pipeline.execute(claim)
        except DomainError as error:
            if not error.failure.retryable:
                await self._metadata.fail_task(task_id, error.failure, now)
            return IngestionExecution(claimed=True, completed=False, failure=error.failure)
        except Exception as error:
            failure = DomainFailure(
                code="INGESTION_RETRYABLE",
                message=str(error) or type(error).__name__,
                retryable=True,
            )
            return IngestionExecution(claimed=True, completed=False, failure=failure)

        if await self._metadata.complete_ingestion(task_id, chunks, now):
            return IngestionExecution(claimed=True, completed=True)
        return IngestionExecution(
            claimed=True,
            completed=False,
            failure=DomainFailure(
                code="COMPLETION_FENCE_MISMATCH",
                message="task completion was rejected by its state or document generation fence",
                retryable=False,
            ),
        )
