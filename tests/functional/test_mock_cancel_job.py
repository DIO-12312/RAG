from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rag_mvp.outbox.relay import relay_once
from rag_mvp.rpc.generated import rag_service_pb2
from tests.fakes.container import MockFunctionalHarness
from tests.functional.test_mock_upload_ingest_retrieve import _stub, _upload


@pytest.mark.asyncio
@pytest.mark.functional
async def test_cancel_rpc_stops_pending_ingestion_and_is_idempotent(tmp_path) -> None:
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
            _upload(created.result.dataset_id, idempotency_key="submit", content=b"cancel me")
        )
        request = rag_service_pb2.CancelJobRequest(
            context=rag_service_pb2.RequestContext(
                request_id="cancel", idempotency_key="cancel-key"
            ),
            job_id=submitted.result.job_id,
        )

        cancelled = await stub.CancelJob(request)
        repeated = await stub.CancelJob(request)

        assert cancelled.result.job_status == rag_service_pb2.JOB_STATUS_CANCELLED
        assert cancelled.result.task_status == rag_service_pb2.TASK_STATUS_CANCELLED
        assert cancelled.result.cancel_requested is True
        assert repeated.result.job_status == rag_service_pb2.JOB_STATUS_CANCELLED
        assert await relay_once(harness.metadata, harness.queue, now, limit=10) == 0
