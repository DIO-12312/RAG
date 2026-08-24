"""Protobuf-to-application RPC boundary for the RAG service."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC, datetime

from rag_mvp.application.document_service import DocumentService
from rag_mvp.application.dto import (
    CancelJobCommand,
    CreateDatasetCommand,
    DeleteDocumentCommand,
    GetJobQuery,
    JobView,
    RetrieveQuery,
    RetryJobCommand,
    SubmitDocumentCommand,
)
from rag_mvp.application.job_service import JobService
from rag_mvp.application.retrieval_service import RetrievalService
from rag_mvp.domain.enums import DocumentStatus, JobStatus, JobType, TaskStatus
from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.domain.models import Evidence, Locator, ScoreBreakdown
from rag_mvp.observability import emit_event
from rag_mvp.retrieval.context_builder import ContextPlan
from rag_mvp.rpc.generated import rag_service_pb2
from rag_mvp.rpc.interceptors import business_error

FEATURE_NOT_AVAILABLE = "FEATURE_NOT_AVAILABLE"

_JOB_STATUS: Mapping[JobStatus, rag_service_pb2.JobStatus] = {
    JobStatus.PENDING: rag_service_pb2.JOB_STATUS_PENDING,
    JobStatus.RUNNING: rag_service_pb2.JOB_STATUS_RUNNING,
    JobStatus.SUCCEEDED: rag_service_pb2.JOB_STATUS_SUCCEEDED,
    JobStatus.FAILED: rag_service_pb2.JOB_STATUS_FAILED,
    JobStatus.CANCELLED: rag_service_pb2.JOB_STATUS_CANCELLED,
}
_TASK_STATUS: Mapping[TaskStatus, rag_service_pb2.TaskStatus] = {
    TaskStatus.PENDING: rag_service_pb2.TASK_STATUS_PENDING,
    TaskStatus.RUNNING: rag_service_pb2.TASK_STATUS_RUNNING,
    TaskStatus.SUCCEEDED: rag_service_pb2.TASK_STATUS_SUCCEEDED,
    TaskStatus.FAILED: rag_service_pb2.TASK_STATUS_FAILED,
    TaskStatus.CANCELLED: rag_service_pb2.TASK_STATUS_CANCELLED,
}
_JOB_TYPE: Mapping[JobType, rag_service_pb2.JobType] = {
    JobType.INGEST_DOCUMENT: rag_service_pb2.JOB_TYPE_INGEST_DOCUMENT,
    JobType.DELETE_DOCUMENT: rag_service_pb2.JOB_TYPE_DELETE_DOCUMENT,
    JobType.CLEANUP_INDEX_VERSION: rag_service_pb2.JOB_TYPE_CLEANUP_INDEX_VERSION,
}
_DOCUMENT_STATUS: Mapping[DocumentStatus, rag_service_pb2.DocumentStatus] = {
    DocumentStatus.PENDING: rag_service_pb2.DOCUMENT_STATUS_PENDING,
    DocumentStatus.READY: rag_service_pb2.DOCUMENT_STATUS_READY,
    DocumentStatus.FAILED: rag_service_pb2.DOCUMENT_STATUS_FAILED,
    DocumentStatus.DELETED: rag_service_pb2.DOCUMENT_STATUS_DELETED,
}


def _unavailable(request_id: str) -> rag_service_pb2.BusinessError:
    return business_error(
        DomainFailure(
            FEATURE_NOT_AVAILABLE,
            "This capability is not available in the current milestone.",
        ),
        request_id,
    )


def _unexpected(error: Exception, request_id: str) -> rag_service_pb2.BusinessError:
    if isinstance(error, DomainError):
        failure = error.failure
    elif isinstance(error, ValueError):
        failure = DomainFailure("INVALID_ARGUMENT", str(error))
    else:
        failure = DomainFailure("INTERNAL_ERROR", "internal RAG service error", retryable=True)
    emit_event(
        "rpc_failed",
        request_id=request_id,
        stage="rpc",
        duration_ms=0.0,
        error_code=failure.code,
    )
    return business_error(failure, request_id)


def _job_result(view: JobView) -> rag_service_pb2.JobResult:
    result = rag_service_pb2.JobResult(
        job_id=view.job_id,
        document_id=view.document_id,
        type=_JOB_TYPE[view.type],
        status=_JOB_STATUS[view.status],
        progress=view.progress,
        retryable=view.retryable,
        retry_count=view.retry_count,
        cancel_requested=view.cancel_requested,
        task_status=_TASK_STATUS[view.task_status],
    )
    if view.failure is not None:
        result.failure.CopyFrom(
            rag_service_pb2.JobFailure(
                code=view.failure.code,
                message=view.failure.message,
                retryable=view.failure.retryable,
            )
        )
    return result


def _locator(locator: Locator) -> rag_service_pb2.Locator:
    result = rag_service_pb2.Locator(metadata=dict(locator.metadata))
    if locator.page_number is not None:
        result.page_number = locator.page_number
    if locator.start_line is not None:
        result.start_line = locator.start_line
    if locator.end_line is not None:
        result.end_line = locator.end_line
    if locator.symbol is not None:
        result.symbol = locator.symbol
    if locator.language is not None:
        result.language = locator.language
    return result


def _scores(scores: ScoreBreakdown) -> rag_service_pb2.ScoreBreakdown:
    result = rag_service_pb2.ScoreBreakdown()
    if scores.dense_score is not None:
        result.dense_score = scores.dense_score
    if scores.sparse_score is not None:
        result.sparse_score = scores.sparse_score
    if scores.fusion_score is not None:
        result.fusion_score = scores.fusion_score
    if scores.rerank_score is not None:
        result.rerank_score = scores.rerank_score
    return result


def _evidence(evidence: Evidence) -> rag_service_pb2.Evidence:
    return rag_service_pb2.Evidence(
        chunk_id=evidence.chunk_id,
        document_id=evidence.document_id,
        content_with_weight=evidence.content_with_weight,
        source_name=evidence.source_name,
        locator=_locator(evidence.locator),
        metadata=dict(evidence.metadata),
        scores=_scores(evidence.scores),
        index_version=evidence.index_version,
    )


def _retrieve_result(plan: ContextPlan) -> rag_service_pb2.RetrieveResult:
    return rag_service_pb2.RetrieveResult(
        evidence=[_evidence(item) for item in plan.evidence],
        estimated_tokens=plan.estimated_tokens,
        omitted_chunk_ids=plan.omitted_chunk_ids,
    )


def _filters(request: rag_service_pb2.RetrieveRequest) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in request.filters:
        if not item.key:
            raise DomainError(DomainFailure("INVALID_FILTER", "filter key is required"))
        existing = result.get(item.key)
        if existing is not None and existing != item.value:
            raise DomainError(
                DomainFailure("INVALID_FILTER", "duplicate filter keys must have one value")
            )
        result[item.key] = item.value
    return result


class RagService:
    """Milestone B transport adapter with explicitly injected application services."""

    def __init__(
        self,
        *,
        documents: DocumentService | None = None,
        jobs: JobService | None = None,
        retrieval: RetrievalService | None = None,
        now: Callable[[], datetime] | None = None,
        parser_version: str = "source-router-v1",
        chunk_size: int = 800,
        chunk_overlap: int = 120,
        embedding_model: str | None = None,
    ) -> None:
        if not parser_version.strip():
            raise ValueError("parser_version must not be empty")
        if chunk_size < 1:
            raise ValueError("chunk_size must be at least 1")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")
        self._documents = documents
        self._jobs = jobs
        self._retrieval = retrieval
        self._now = now or (lambda: datetime.now(UTC))
        self._parser_version = parser_version
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._embedding_model = embedding_model

    async def CreateDataset(
        self,
        request: rag_service_pb2.CreateDatasetRequest,
        context: object,
    ) -> rag_service_pb2.CreateDatasetResponse:
        del context
        if self._documents is None:
            return rag_service_pb2.CreateDatasetResponse(
                error=_unavailable(request.context.request_id)
            )
        try:
            result = await self._documents.create_dataset(
                CreateDatasetCommand(
                    request_id=request.context.request_id,
                    idempotency_key=request.context.idempotency_key,
                    name=request.name,
                    embedding_model=request.embedding_model,
                    embedding_dimension=request.embedding_dimension,
                    now=self._now(),
                )
            )
            return rag_service_pb2.CreateDatasetResponse(
                result=rag_service_pb2.CreateDatasetResult(
                    dataset_id=result.dataset_id,
                    name=result.name,
                    embedding_model=result.embedding_model,
                    embedding_dimension=result.embedding_dimension,
                )
            )
        except Exception as error:
            return rag_service_pb2.CreateDatasetResponse(
                error=_unexpected(error, request.context.request_id)
            )

    async def SubmitDocument(
        self,
        request_iterator: AsyncIterator[rag_service_pb2.UploadDocumentRequest],
        context: object,
    ) -> rag_service_pb2.SubmitDocumentResponse:
        del context
        if self._documents is None:
            return rag_service_pb2.SubmitDocumentResponse(error=_unavailable(""))
        request_id = ""
        try:
            header: rag_service_pb2.UploadHeader | None = None
            content = bytearray()
            async for frame in request_iterator:
                payload = frame.WhichOneof("payload")
                if header is None:
                    if payload != "header":
                        raise DomainError(
                            DomainFailure(
                                "INVALID_UPLOAD_STREAM", "the first upload frame must be a header"
                            )
                        )
                    header = frame.header
                    request_id = header.context.request_id
                elif payload != "data":
                    raise DomainError(
                        DomainFailure(
                            "INVALID_UPLOAD_STREAM", "upload frames after the header must be data"
                        )
                    )
                else:
                    content.extend(frame.data)
            if header is None:
                raise DomainError(
                    DomainFailure("INVALID_UPLOAD_STREAM", "upload stream requires a header")
                )
            result = await self._documents.submit_document(
                SubmitDocumentCommand(
                    request_id=header.context.request_id,
                    idempotency_key=header.context.idempotency_key,
                    dataset_id=header.dataset_id,
                    source_name=header.source_name,
                    content=bytes(content),
                    expected_sha256=(
                        header.expected_sha256 if header.HasField("expected_sha256") else None
                    ),
                    target_document_id=(
                        header.target_document_id if header.HasField("target_document_id") else None
                    ),
                    parser_version=self._parser_version,
                    chunk_size=self._chunk_size,
                    chunk_overlap=self._chunk_overlap,
                    embedding_model=self._embedding_model,
                    now=self._now(),
                )
            )
            return rag_service_pb2.SubmitDocumentResponse(
                result=rag_service_pb2.SubmitDocumentResult(
                    document_id=result.document_id,
                    job_id=result.job_id,
                    reused=result.reused,
                )
            )
        except Exception as error:
            return rag_service_pb2.SubmitDocumentResponse(error=_unexpected(error, request_id))

    async def GetJob(
        self,
        request: rag_service_pb2.GetJobRequest,
        context: object,
    ) -> rag_service_pb2.GetJobResponse:
        del context
        if self._jobs is None:
            return rag_service_pb2.GetJobResponse(error=_unavailable(request.request_id))
        try:
            view = await self._jobs.get_job(GetJobQuery(request.request_id, request.job_id))
            return rag_service_pb2.GetJobResponse(result=_job_result(view))
        except Exception as error:
            return rag_service_pb2.GetJobResponse(error=_unexpected(error, request.request_id))

    async def RetryJob(
        self,
        request: rag_service_pb2.RetryJobRequest,
        context: object,
    ) -> rag_service_pb2.RetryJobResponse:
        del context
        if self._jobs is None:
            return rag_service_pb2.RetryJobResponse(error=_unavailable(request.context.request_id))
        try:
            view = await self._jobs.retry_job(
                RetryJobCommand(
                    request_id=request.context.request_id,
                    idempotency_key=request.context.idempotency_key,
                    job_id=request.job_id,
                    now=self._now(),
                )
            )
            return rag_service_pb2.RetryJobResponse(result=_job_result(view))
        except Exception as error:
            return rag_service_pb2.RetryJobResponse(
                error=_unexpected(error, request.context.request_id)
            )

    async def CancelJob(
        self,
        request: rag_service_pb2.CancelJobRequest,
        context: object,
    ) -> rag_service_pb2.CancelJobResponse:
        del context
        if self._jobs is None:
            return rag_service_pb2.CancelJobResponse(error=_unavailable(request.context.request_id))
        try:
            view = await self._jobs.cancel_job(
                CancelJobCommand(
                    request_id=request.context.request_id,
                    idempotency_key=request.context.idempotency_key,
                    job_id=request.job_id,
                    now=self._now(),
                )
            )
            return rag_service_pb2.CancelJobResponse(
                result=rag_service_pb2.CancelJobResult(
                    job_id=view.job_id,
                    job_status=_JOB_STATUS[view.status],
                    task_status=_TASK_STATUS[view.task_status],
                    cancel_requested=view.cancel_requested,
                )
            )
        except Exception as error:
            return rag_service_pb2.CancelJobResponse(
                error=_unexpected(error, request.context.request_id)
            )

    async def Retrieve(
        self,
        request: rag_service_pb2.RetrieveRequest,
        context: object,
    ) -> rag_service_pb2.RetrieveResponse:
        del context
        if self._retrieval is None:
            return rag_service_pb2.RetrieveResponse(error=_unavailable(request.request_id))
        try:
            plan = await self._retrieval.retrieve(
                RetrieveQuery(
                    request_id=request.request_id,
                    dataset_id=request.dataset_id,
                    query=request.query,
                    top_k=request.top_k or 6,
                    filters=_filters(request),
                    max_context_tokens=request.max_context_tokens or 4000,
                    enable_rerank=request.enable_rerank,
                )
            )
            return rag_service_pb2.RetrieveResponse(result=_retrieve_result(plan))
        except Exception as error:
            return rag_service_pb2.RetrieveResponse(error=_unexpected(error, request.request_id))

    async def DeleteDocument(
        self,
        request: rag_service_pb2.DeleteDocumentRequest,
        context: object,
    ) -> rag_service_pb2.DeleteDocumentResponse:
        del context
        if self._documents is None:
            return rag_service_pb2.DeleteDocumentResponse(
                error=_unavailable(request.context.request_id)
            )
        try:
            result = await self._documents.delete_document(
                DeleteDocumentCommand(
                    request_id=request.context.request_id,
                    idempotency_key=request.context.idempotency_key,
                    document_id=request.document_id,
                    now=self._now(),
                )
            )
            return rag_service_pb2.DeleteDocumentResponse(
                result=rag_service_pb2.DeleteDocumentResult(
                    document_id=result.document_id,
                    job_id=result.job_id,
                    document_status=_DOCUMENT_STATUS[DocumentStatus.DELETED],
                )
            )
        except Exception as error:
            return rag_service_pb2.DeleteDocumentResponse(
                error=_unexpected(error, request.context.request_id)
            )
