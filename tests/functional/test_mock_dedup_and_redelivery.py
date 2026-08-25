from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rag_mvp.ingestion.worker import worker_once
from rag_mvp.outbox.finalizer import finalize_once
from rag_mvp.outbox.relay import relay_once
from rag_mvp.rpc.generated import rag_service_pb2
from tests.fakes.container import MockFunctionalHarness
from tests.functional.test_mock_upload_ingest_retrieve import _stub, _upload


@pytest.mark.asyncio
@pytest.mark.functional
async def test_mock_dedup_and_relay_duplicate_delivery_converge(tmp_path) -> None:
    now = datetime.now(UTC)
    harness = MockFunctionalHarness.build(tmp_path / "objects", now)
    content = b"one canonical retrieval document"

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
        first = await stub.SubmitDocument(
            _upload(created.result.dataset_id, idempotency_key="same-key", content=content)
        )
        same_request = await stub.SubmitDocument(
            _upload(created.result.dataset_id, idempotency_key="same-key", content=content)
        )
        same_fingerprint = await stub.SubmitDocument(
            _upload(created.result.dataset_id, idempotency_key="different-key", content=content)
        )

        assert same_request.result.job_id == first.result.job_id
        assert same_fingerprint.result.job_id == first.result.job_id
        assert same_request.result.reused is True
        assert same_fingerprint.result.reused is True
        assert harness.metadata.counts()["documents"] == 1

        assert await finalize_once(harness.metadata, harness.storage, now, limit=10) == 1

        async def crash_after_publish() -> None:
            raise RuntimeError("relay crashed after publish")

        with pytest.raises(RuntimeError, match="relay crashed"):
            await relay_once(
                harness.metadata,
                harness.queue,
                now,
                limit=10,
                after_publish=crash_after_publish,
            )
        assert await relay_once(harness.metadata, harness.queue, now, limit=10) == 1

        assert await worker_once(
            harness.queue, harness.metadata, harness.ingestion, "worker-1", now
        )
        assert await worker_once(
            harness.queue, harness.metadata, harness.ingestion, "worker-2", now
        )
        completed = await stub.GetJob(
            rag_service_pb2.GetJobRequest(request_id="job", job_id=first.result.job_id)
        )

        assert completed.result.status == rag_service_pb2.JOB_STATUS_SUCCEEDED
        assert harness.search.record_count == 1
        assert harness.search.upsert_calls == 1
        assert harness.model.embed_calls == 1
