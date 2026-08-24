from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rag_mvp.adapters.chunkers.recursive import RecursiveChunker
from rag_mvp.adapters.parsers.text import TextParser
from rag_mvp.application.document_service import DocumentService
from rag_mvp.application.dto import CreateDatasetCommand, SubmitDocumentCommand
from rag_mvp.domain.ids import es_record_id
from rag_mvp.ingestion.pipeline import IngestionPipeline
from rag_mvp.outbox.finalizer import finalize_once
from tests.fakes.metadata import FakeMetadataRepository
from tests.fakes.model import FakeModelGateway
from tests.fakes.search_engine import FakeSearchEngine
from tests.fakes.storage import FakeObjectStorage


@pytest.mark.asyncio
async def test_pipeline_builds_stable_versioned_chunks_and_upserts_search() -> None:
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    storage = FakeObjectStorage()
    model = FakeModelGateway(dimension=8)
    search = FakeSearchEngine()
    documents = DocumentService(repository, storage, max_upload_bytes=1024)
    await documents.create_dataset(
        CreateDatasetCommand("trace", "create", "Docs", "fake", 8, now, "dataset-1")
    )
    submitted = await documents.submit_document(
        SubmitDocumentCommand(
            "trace",
            "submit",
            "dataset-1",
            "guide.txt",
            b"alpha beta gamma delta",
            None,
            None,
            "text-v1",
            12,
            3,
            "fake",
            now,
        )
    )
    await finalize_once(repository, storage, now, limit=10)
    task = next(iter(repository.tasks.values()))
    claim = await repository.claim_task(task.id, delivery_sequence=1, now=now)
    assert claim is not None
    pipeline = IngestionPipeline(
        storage=storage,
        parser=TextParser(),
        chunker=RecursiveChunker(chunk_size=12, overlap=3),
        model=model,
        search=search,
    )

    chunks = await pipeline.execute(claim)

    assert chunks
    assert all(chunk.document_id == submitted.document_id for chunk in chunks)
    assert all(chunk.index_version == 1 for chunk in chunks)
    assert set(search.records) == {
        es_record_id(chunk.document_id, chunk.index_version, chunk.id) for chunk in chunks
    }
    assert model.embed_calls == 1
