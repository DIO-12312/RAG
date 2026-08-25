from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rag_mvp.ingestion.worker import worker_once
from rag_mvp.outbox.relay import relay_once
from rag_mvp.rpc.generated import rag_service_pb2
from tests.fakes.container import MockFunctionalHarness
from tests.functional.test_mock_upload_ingest_retrieve import _stub, _upload


@pytest.mark.asyncio
@pytest.mark.functional
async def test_delete_is_immediately_invisible_then_worker_cleans_storage_and_index(
    tmp_path,
) -> None:
    now = datetime.now(UTC)
    harness = MockFunctionalHarness.build(tmp_path / "objects", now)

    async with _stub(harness) as stub:
        created = await stub.CreateDataset(
            rag_service_pb2.CreateDatasetRequest(
                context=rag_service_pb2.RequestContext(
                    request_id="create", idempotency_key="create"
                ),
                name="Docs",
                embedding_model="fake",
                embedding_dimension=8,
            )
        )
        submitted = await stub.SubmitDocument(
            _upload(
                created.result.dataset_id,
                idempotency_key="submit",
                content=b"document deleted asynchronously",
            )
        )
        await harness.run_ingestion_once()
        document = await harness.metadata.get_document(submitted.result.document_id)
        assert document is not None and document.object_key is not None
        object_key = document.object_key
        assert harness.search.record_count == 1
        assert await harness.storage.exists(object_key)

        deleted = await stub.DeleteDocument(
            rag_service_pb2.DeleteDocumentRequest(
                context=rag_service_pb2.RequestContext(
                    request_id="delete", idempotency_key="delete-key"
                ),
                document_id=submitted.result.document_id,
            )
        )
        invisible = await stub.Retrieve(
            rag_service_pb2.RetrieveRequest(
                request_id="retrieve-after-delete",
                dataset_id=created.result.dataset_id,
                query="document",
                top_k=6,
                max_context_tokens=100,
            )
        )

        assert deleted.result.document_status == rag_service_pb2.DOCUMENT_STATUS_DELETED
        assert list(invisible.result.evidence) == []
        assert harness.search.record_count == 1
        assert await harness.storage.exists(object_key)

        assert await relay_once(harness.metadata, harness.queue, now, limit=10) == 1
        assert await worker_once(
            harness.queue,
            harness.metadata,
            harness.ingestion,
            "cleanup-worker",
            now,
            cleanup=harness.cleanup,
        )
        cleanup_job = await stub.GetJob(
            rag_service_pb2.GetJobRequest(request_id="cleanup", job_id=deleted.result.job_id)
        )

        assert cleanup_job.result.status == rag_service_pb2.JOB_STATUS_SUCCEEDED
        assert harness.search.record_count == 0
        assert not await harness.storage.exists(object_key)
