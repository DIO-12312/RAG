from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import grpc
import pytest

from rag_mvp.rpc.generated import rag_service_pb2, rag_service_pb2_grpc
from tests.fakes.container import MockFunctionalHarness


@asynccontextmanager
async def _stub(
    harness: MockFunctionalHarness,
) -> AsyncIterator[rag_service_pb2_grpc.RagServiceStub]:
    server = grpc.aio.server()
    rag_service_pb2_grpc.add_RagServiceServicer_to_server(harness.rpc, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    try:
        yield rag_service_pb2_grpc.RagServiceStub(channel)
    finally:
        await channel.close()
        await server.stop(0)


async def _upload(
    dataset_id: str,
    *,
    idempotency_key: str,
    content: bytes,
    source_name: str = "guide.txt",
) -> AsyncIterator[rag_service_pb2.UploadDocumentRequest]:
    yield rag_service_pb2.UploadDocumentRequest(
        header=rag_service_pb2.UploadHeader(
            context=rag_service_pb2.RequestContext(
                request_id=f"request-{idempotency_key}", idempotency_key=idempotency_key
            ),
            dataset_id=dataset_id,
            source_name=source_name,
        )
    )
    midpoint = len(content) // 2
    yield rag_service_pb2.UploadDocumentRequest(data=content[:midpoint])
    yield rag_service_pb2.UploadDocumentRequest(data=content[midpoint:])


@pytest.mark.asyncio
@pytest.mark.functional
async def test_mock_grpc_upload_async_ingest_and_dense_retrieve(tmp_path) -> None:
    harness = MockFunctionalHarness.build(tmp_path / "objects", datetime.now(UTC))
    content = "第一行：RAG 使用向量检索。\n第二行：Evidence 保留来源。".encode()

    async with _stub(harness) as stub:
        created = await stub.CreateDataset(
            rag_service_pb2.CreateDatasetRequest(
                context=rag_service_pb2.RequestContext(
                    request_id="request-create", idempotency_key="create-dataset"
                ),
                name="Docs",
                embedding_model="fake",
                embedding_dimension=8,
            )
        )
        submitted = await stub.SubmitDocument(
            _upload(created.result.dataset_id, idempotency_key="submit-document", content=content)
        )
        pending = await stub.GetJob(
            rag_service_pb2.GetJobRequest(
                request_id="request-pending", job_id=submitted.result.job_id
            )
        )

        assert pending.result.status == rag_service_pb2.JOB_STATUS_PENDING
        await harness.run_ingestion_once()

        succeeded = await stub.GetJob(
            rag_service_pb2.GetJobRequest(
                request_id="request-succeeded", job_id=submitted.result.job_id
            )
        )
        retrieved = await stub.Retrieve(
            rag_service_pb2.RetrieveRequest(
                request_id="request-retrieve",
                dataset_id=created.result.dataset_id,
                query="Evidence 来源",
                top_k=6,
                max_context_tokens=1000,
            )
        )
        reranked = await stub.Retrieve(
            rag_service_pb2.RetrieveRequest(
                request_id="request-rerank",
                dataset_id=created.result.dataset_id,
                query="Evidence 来源",
                top_k=6,
                enable_rerank=True,
                max_context_tokens=1000,
            )
        )

        assert succeeded.result.status == rag_service_pb2.JOB_STATUS_SUCCEEDED
        assert succeeded.result.task_status == rag_service_pb2.TASK_STATUS_SUCCEEDED
        assert len(retrieved.result.evidence) == 1
        evidence = retrieved.result.evidence[0]
        assert evidence.document_id == submitted.result.document_id
        assert evidence.content_with_weight == content.decode()
        assert evidence.source_name == "guide.txt"
        assert evidence.locator.start_line == 1
        assert evidence.locator.end_line == 2
        assert evidence.scores.HasField("dense_score")
        assert evidence.index_version == 1
        assert reranked.result.evidence[0].scores.HasField("rerank_score")

        retry = await stub.RetryJob(rag_service_pb2.RetryJobRequest())
        cancel = await stub.CancelJob(rag_service_pb2.CancelJobRequest())
        delete = await stub.DeleteDocument(rag_service_pb2.DeleteDocumentRequest())
        assert retry.error.code == "IDEMPOTENCY_KEY_REQUIRED"
        assert cancel.error.code == "IDEMPOTENCY_KEY_REQUIRED"
        assert delete.error.code == "IDEMPOTENCY_KEY_REQUIRED"
