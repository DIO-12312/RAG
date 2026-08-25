from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO

import pytest
from reportlab.pdfgen.canvas import Canvas

from rag_mvp.ingestion.worker import worker_once
from rag_mvp.outbox.finalizer import finalize_once
from rag_mvp.outbox.relay import relay_once
from rag_mvp.rpc.generated import rag_service_pb2
from tests.fakes.container import MockFunctionalHarness
from tests.functional.test_mock_upload_ingest_retrieve import _stub, _upload


def _pdf_bytes() -> bytes:
    buffer = BytesIO()
    canvas = Canvas(buffer)
    canvas.drawString(72, 720, "pdfanchor evidence")
    canvas.save()
    return buffer.getvalue()


@pytest.mark.asyncio
@pytest.mark.functional
async def test_four_supported_formats_return_precise_provenance(tmp_path) -> None:
    now = datetime.now(UTC)
    harness = MockFunctionalHarness.build(tmp_path / "objects", now)
    sources = (
        ("notes.txt", b"txtanchor evidence"),
        ("guide.md", b"# Retrieval\nmarkdownanchor evidence"),
        ("main.py", b"def codeanchor():\n    return codeanchor"),
        ("paper.pdf", _pdf_bytes()),
    )

    async with _stub(harness) as stub:
        created = await stub.CreateDataset(
            rag_service_pb2.CreateDatasetRequest(
                context=rag_service_pb2.RequestContext(
                    request_id="create", idempotency_key="create"
                ),
                name="Four formats",
                embedding_model="fake",
                embedding_dimension=8,
            )
        )
        for index, (source_name, content) in enumerate(sources):
            submitted = await stub.SubmitDocument(
                _upload(
                    created.result.dataset_id,
                    idempotency_key=f"submit-{index}",
                    source_name=source_name,
                    content=content,
                )
            )
            assert submitted.WhichOneof("outcome") == "result"

        assert await finalize_once(harness.metadata, harness.storage, now, limit=10) == 4
        assert await relay_once(harness.metadata, harness.queue, now, limit=10) == 4
        for index in range(4):
            assert await worker_once(
                harness.queue,
                harness.metadata,
                harness.ingestion,
                f"worker-{index}",
                now,
                cleanup=harness.cleanup,
            )

        results = {}
        for anchor in ("txtanchor", "markdownanchor", "codeanchor", "pdfanchor"):
            response = await stub.Retrieve(
                rag_service_pb2.RetrieveRequest(
                    request_id=f"retrieve-{anchor}",
                    dataset_id=created.result.dataset_id,
                    query=anchor,
                    top_k=1,
                    max_context_tokens=100,
                )
            )
            assert len(response.result.evidence) == 1
            results[anchor] = response.result.evidence[0]

        assert results["txtanchor"].source_name == "notes.txt"
        assert results["txtanchor"].locator.start_line == 1
        assert results["markdownanchor"].metadata["section"] == "Retrieval"
        assert results["markdownanchor"].locator.start_line == 1
        assert results["codeanchor"].locator.symbol == "codeanchor"
        assert results["codeanchor"].locator.language == "python"
        assert results["pdfanchor"].locator.page_number == 1
