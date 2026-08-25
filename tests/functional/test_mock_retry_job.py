from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rag_mvp.domain.errors import DomainFailure
from rag_mvp.ingestion.worker import worker_once
from rag_mvp.outbox.finalizer import finalize_once
from rag_mvp.outbox.relay import relay_once
from rag_mvp.rpc.generated import rag_service_pb2
from tests.fakes.container import MockFunctionalHarness
from tests.functional.test_mock_upload_ingest_retrieve import _stub, _upload


@pytest.mark.asyncio
@pytest.mark.functional
async def test_retry_rpc_creates_new_job_and_worker_completes_it(tmp_path) -> None:
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
                content=b"retryable retrieval source",
            )
        )
        await finalize_once(harness.metadata, harness.storage, now, limit=10)
        original_task = await harness.metadata.get_task_for_job(submitted.result.job_id)
        assert original_task is not None
        original_event = (await harness.metadata.list_ready_outbox(1))[0]
        await harness.metadata.mark_outbox_published(original_event.id, now)
        await harness.metadata.claim_task(original_task.id, 1, now)
        await harness.metadata.fail_task(
            original_task.id,
            DomainFailure("MODEL_UNAVAILABLE", "temporary", retryable=True),
            now,
        )

        retry = await stub.RetryJob(
            rag_service_pb2.RetryJobRequest(
                context=rag_service_pb2.RequestContext(
                    request_id="retry", idempotency_key="retry-key"
                ),
                job_id=submitted.result.job_id,
            )
        )
        assert retry.WhichOneof("outcome") == "result"
        assert retry.result.job_id != submitted.result.job_id
        assert retry.result.status == rag_service_pb2.JOB_STATUS_PENDING

        assert await relay_once(harness.metadata, harness.queue, now, limit=10) == 1
        assert await worker_once(
            harness.queue, harness.metadata, harness.ingestion, "retry-worker", now
        )
        completed = await stub.GetJob(
            rag_service_pb2.GetJobRequest(request_id="get-retry", job_id=retry.result.job_id)
        )
        original = await stub.GetJob(
            rag_service_pb2.GetJobRequest(request_id="get-original", job_id=submitted.result.job_id)
        )

        assert completed.result.status == rag_service_pb2.JOB_STATUS_SUCCEEDED
        assert original.result.status == rag_service_pb2.JOB_STATUS_FAILED
