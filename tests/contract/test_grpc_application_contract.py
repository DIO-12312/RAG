from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import grpc
import pytest

from rag_mvp.application.document_service import DocumentService
from rag_mvp.application.job_service import JobService
from rag_mvp.application.retrieval_service import RetrievalService
from rag_mvp.rpc.generated import rag_service_pb2, rag_service_pb2_grpc
from rag_mvp.rpc.rag_service import RagService
from tests.fakes.metadata import FakeMetadataRepository
from tests.fakes.model import FakeModelGateway
from tests.fakes.search_engine import FakeSearchEngine
from tests.fakes.storage import FakeObjectStorage


def _service() -> tuple[RagService, FakeMetadataRepository]:
    repository = FakeMetadataRepository()
    storage = FakeObjectStorage()
    documents = DocumentService(repository, storage, max_upload_bytes=1024)
    service = RagService(
        documents=documents,
        jobs=JobService(repository),
        retrieval=RetrievalService(repository, FakeSearchEngine(), FakeModelGateway(8)),
        now=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    return service, repository


async def _upload_frames(dataset_id: str) -> AsyncIterator[rag_service_pb2.UploadDocumentRequest]:
    yield rag_service_pb2.UploadDocumentRequest(
        header=rag_service_pb2.UploadHeader(
            context=rag_service_pb2.RequestContext(
                request_id="request-submit", idempotency_key="submit-key"
            ),
            dataset_id=dataset_id,
            source_name="guide.txt",
        )
    )
    yield rag_service_pb2.UploadDocumentRequest(data=b"hello ")
    yield rag_service_pb2.UploadDocumentRequest(data=b"retrieval")


@pytest.mark.asyncio
async def test_open_rpc_methods_convert_application_results() -> None:
    service, _ = _service()
    created = await service.CreateDataset(
        rag_service_pb2.CreateDatasetRequest(
            context=rag_service_pb2.RequestContext(
                request_id="request-create", idempotency_key="create-key"
            ),
            name="Docs",
            embedding_model="fake",
            embedding_dimension=8,
        ),
        None,
    )
    submitted = await service.SubmitDocument(_upload_frames(created.result.dataset_id), None)
    job = await service.GetJob(
        rag_service_pb2.GetJobRequest(request_id="request-job", job_id=submitted.result.job_id),
        None,
    )
    retrieved = await service.Retrieve(
        rag_service_pb2.RetrieveRequest(
            request_id="request-retrieve",
            dataset_id=created.result.dataset_id,
            query="hello",
            top_k=5,
            max_context_tokens=100,
        ),
        None,
    )

    assert created.WhichOneof("outcome") == "result"
    assert created.result.dataset_id
    assert submitted.WhichOneof("outcome") == "result"
    assert submitted.result.document_id
    assert job.result.status == rag_service_pb2.JOB_STATUS_PENDING
    assert job.result.task_status == rag_service_pb2.TASK_STATUS_PENDING
    assert retrieved.WhichOneof("outcome") == "result"
    assert list(retrieved.result.evidence) == []


@pytest.mark.asyncio
async def test_rpc_maps_domain_failures_and_keeps_future_methods_closed() -> None:
    service, _ = _service()
    missing = await service.GetJob(
        rag_service_pb2.GetJobRequest(request_id="request-job", job_id="missing"), None
    )
    retry = await service.RetryJob(
        rag_service_pb2.RetryJobRequest(
            context=rag_service_pb2.RequestContext(request_id="request-retry")
        ),
        None,
    )

    assert missing.error.code == "JOB_NOT_FOUND"
    assert missing.error.request_id == "request-job"
    assert retry.error.code == "FEATURE_NOT_AVAILABLE"


@pytest.mark.asyncio
async def test_submit_document_rejects_data_before_header() -> None:
    service, _ = _service()

    async def invalid_frames() -> AsyncIterator[rag_service_pb2.UploadDocumentRequest]:
        yield rag_service_pb2.UploadDocumentRequest(data=b"orphan")

    response = await service.SubmitDocument(invalid_frames(), None)

    assert response.error.code == "INVALID_UPLOAD_STREAM"


@pytest.mark.asyncio
async def test_open_methods_work_through_generated_grpc_transport() -> None:
    service, _ = _service()
    server = grpc.aio.server()
    rag_service_pb2_grpc.add_RagServiceServicer_to_server(service, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    try:
        stub = rag_service_pb2_grpc.RagServiceStub(channel)
        created = await stub.CreateDataset(
            rag_service_pb2.CreateDatasetRequest(
                context=rag_service_pb2.RequestContext(
                    request_id="transport-create", idempotency_key="transport-key"
                ),
                name="Transport Docs",
                embedding_model="fake",
                embedding_dimension=8,
            )
        )

        assert created.WhichOneof("outcome") == "result"
        assert created.result.name == "Transport Docs"
    finally:
        await channel.close()
        await server.stop(0)
