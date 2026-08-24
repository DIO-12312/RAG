"""Job query use cases independent of protobuf and persistence SDKs."""

from __future__ import annotations

from rag_mvp.application.dto import GetJobQuery, JobView
from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.ports.metadata import MetadataRepository


class JobService:
    def __init__(self, metadata: MetadataRepository) -> None:
        self._metadata = metadata

    async def get_job(self, query: GetJobQuery) -> JobView:
        job = await self._metadata.get_job(query.job_id)
        if job is None:
            raise DomainError(DomainFailure("JOB_NOT_FOUND", "job does not exist"))
        task = await self._metadata.get_task_for_job(job.id)
        if task is None:
            raise DomainError(
                DomainFailure(
                    "JOB_TASK_NOT_FOUND",
                    "job task is temporarily unavailable",
                    retryable=True,
                )
            )
        return JobView(
            job_id=job.id,
            document_id=job.document_id,
            type=job.type,
            status=job.status,
            progress=job.progress,
            failure=job.error,
            retryable=job.retryable,
            retry_count=job.retry_count,
            cancel_requested=job.cancel_requested_at is not None,
            task_status=task.status,
        )
