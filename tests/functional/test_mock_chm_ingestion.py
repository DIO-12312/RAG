from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rag_mvp.adapters.parsers.chm import ChmEntry, ChmParser
from rag_mvp.ingestion.worker import worker_once
from rag_mvp.outbox.finalizer import finalize_once
from rag_mvp.outbox.relay import relay_once
from rag_mvp.rpc.generated import rag_service_pb2
from tests.fakes.container import MockFunctionalHarness
from tests.functional.test_mock_upload_ingest_retrieve import _stub, _upload


class _OneTopicExtractor:
    async def extract(self, content: bytes) -> tuple[ChmEntry, ...]:
        assert content == b"compiled-help"
        return (
            ChmEntry(
                "setup/install.html",
                (
                    '<html><head><meta charset="utf-8"><title>部署指南</title></head>'
                    '<body><h1 id="docker">Docker 部署</h1>'
                    "<p>chmanchor 使用 gRPC 上传并建立知识库。</p></body></html>"
                ).encode(),
            ),
        )


@pytest.mark.asyncio
@pytest.mark.functional
async def test_chm_upload_ingest_retrieve_returns_topic_provenance(tmp_path) -> None:
    now = datetime.now(UTC)
    chm_parser = ChmParser(_OneTopicExtractor())
    harness = MockFunctionalHarness.build(
        tmp_path / "objects",
        now,
        parser=chm_parser,
    )

    async with _stub(harness) as stub:
        created = await stub.CreateDataset(
            rag_service_pb2.CreateDatasetRequest(
                context=rag_service_pb2.RequestContext(
                    request_id="create-chm", idempotency_key="create-chm"
                ),
                name="CHM knowledge",
                embedding_model="fake",
                embedding_dimension=8,
            )
        )
        submitted = await stub.SubmitDocument(
            _upload(
                created.result.dataset_id,
                idempotency_key="submit-chm",
                source_name="manual.chm",
                content=b"compiled-help",
            )
        )
        assert await finalize_once(harness.metadata, harness.storage, now, limit=10) == 1
        assert await relay_once(harness.metadata, harness.queue, now, limit=10) == 1
        assert await worker_once(
            harness.queue,
            harness.metadata,
            harness.ingestion,
            "chm-worker",
            now,
            cleanup=harness.cleanup,
        )

        job = await stub.GetJob(
            rag_service_pb2.GetJobRequest(
                request_id="get-chm-job",
                job_id=submitted.result.job_id,
            )
        )
        response = await stub.Retrieve(
            rag_service_pb2.RetrieveRequest(
                request_id="retrieve-chm",
                dataset_id=created.result.dataset_id,
                query="chmanchor",
                top_k=1,
                max_context_tokens=1000,
            )
        )

    assert job.result.status == rag_service_pb2.JOB_STATUS_SUCCEEDED
    evidence = response.result.evidence[0]
    assert evidence.document_id == submitted.result.document_id
    assert evidence.source_name == "manual.chm"
    assert evidence.metadata["logical_document_type"] == "chm_topic"
    assert evidence.metadata["topic_path"] == "setup/install.html"
    assert evidence.metadata["heading_path"] == "Docker 部署"
    assert evidence.locator.symbol == "Docker 部署"
    assert evidence.locator.language == "html"
    assert evidence.locator.metadata["anchor"] == "docker"
