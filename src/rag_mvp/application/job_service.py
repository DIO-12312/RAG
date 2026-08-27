"""Job query use cases independent of protobuf and persistence SDKs."""

from __future__ import annotations

from rag_mvp.application.dto import CancelJobCommand, GetJobQuery, JobView, RetryJobCommand
from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.domain.models import Job, Task
from rag_mvp.ports.metadata import CancelJobRequest, MetadataRepository, RetryJobRequest


class JobService:
    def __init__(self, metadata: MetadataRepository, *, max_user_retries: int = 3) -> None:
        if max_user_retries < 1:
            raise ValueError("max_user_retries must be at least 1")
        self._metadata = metadata
        self._max_user_retries = max_user_retries

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
        return self._view(job, task)

    async def retry_job(self, command: RetryJobCommand) -> JobView:
        if not command.idempotency_key:
            raise DomainError(
                DomainFailure("IDEMPOTENCY_KEY_REQUIRED", "idempotency key is required")
            )
        retried = await self._metadata.retry_job(
            RetryJobRequest(
                idempotency_key=command.idempotency_key,
                job_id=command.job_id,
                now=command.now,
                max_user_retries=self._max_user_retries,
            )
        )
        job = await self._metadata.get_job(retried.job_id)
        task = await self._metadata.get_task(retried.task_id)
        if job is None or task is None:
            raise DomainError(
                DomainFailure("RETRY_STATE_NOT_FOUND", "retry state is unavailable", retryable=True)
            )
        return self._view(job, task)

    async def cancel_job(self, command: CancelJobCommand) -> JobView:
        if not command.idempotency_key:
            raise DomainError(
                DomainFailure("IDEMPOTENCY_KEY_REQUIRED", "idempotency key is required")
            )
        cancelled = await self._metadata.cancel_job(
            CancelJobRequest(command.idempotency_key, command.job_id, command.now)
        )
        job = await self._metadata.get_job(cancelled.job_id)
        task = await self._metadata.get_task_for_job(cancelled.job_id)
        if job is None or task is None:
            raise DomainError(
                DomainFailure(
                    "CANCEL_STATE_NOT_FOUND", "cancel state is unavailable", retryable=True
                )
            )
        return self._view(job, task)

    @staticmethod
    def _view(job: Job, task: Task) -> JobView:
        return JobView(
            job_id=job.id,
            document_id=job.document_id,
            dataset_id=job.dataset_id,
            type=job.type,
            status=job.status,
            progress=job.progress,
            failure=job.error,
            retryable=job.retryable,
            retry_count=job.retry_count,
            cancel_requested=job.cancel_requested_at is not None,
            task_status=task.status,
        )
