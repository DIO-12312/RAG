from __future__ import annotations

# 验证解析、规范化、切块、向量化与写索引的单 Task 流水线。
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from rag_mvp.adapters.chunkers.recursive import RecursiveChunker
from rag_mvp.adapters.parsers.text import TextParser
from rag_mvp.application.document_service import DocumentService
from rag_mvp.application.dto import CreateDatasetCommand, SubmitDocumentCommand
from rag_mvp.domain.ids import es_record_id
from rag_mvp.domain.models import Locator
from rag_mvp.ingestion.pipeline import IngestionPipeline
from rag_mvp.outbox.finalizer import finalize_once
from rag_mvp.ports.chunker import ChunkDraft
from rag_mvp.ports.parser import ParsedSegment
from tests.fakes.metadata import FakeMetadataRepository
from tests.fakes.model import FakeModelGateway
from tests.fakes.search_engine import FakeSearchEngine
from tests.fakes.storage import FakeObjectStorage


class _DuplicateChunker:
    async def split(self, segments: Sequence[ParsedSegment]) -> tuple[ChunkDraft, ...]:
        assert segments
        return (
            ChunkDraft(
                ordinal=0,
                content_with_weight="repeated evidence",
                locator=Locator(start_line=1, end_line=1),
                metadata={"position": "first"},
            ),
            ChunkDraft(
                ordinal=1,
                content_with_weight="repeated evidence",
                locator=Locator(start_line=2, end_line=2),
                metadata={"position": "second"},
            ),
            ChunkDraft(
                ordinal=2,
                content_with_weight="unique evidence",
                locator=Locator(start_line=3, end_line=3),
                metadata={"position": "third"},
            ),
        )


class _RecordingModel(FakeModelGateway):
    def __init__(self, dimension: int = 8) -> None:
        super().__init__(dimension)
        self.embedded_batches: list[tuple[str, ...]] = []

    async def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        self.embedded_batches.append(tuple(texts))
        return await super().embed(texts)


class _HierarchyChunker:
    async def split(self, segments: Sequence[ParsedSegment]) -> tuple[ChunkDraft, ...]:
        assert segments
        common = {
            "source_type": "chm",
            "topic_path": "topic.html",
            "section_id": "section-child",
            "parent_section_id": "section-root",
        }
        return (
            ChunkDraft(
                0,
                "root overview",
                Locator(start_line=1, end_line=3),
                {
                    **common,
                    "section_id": "section-root",
                    "parent_section_id": "",
                    "chunk_role": "section_parent",
                },
            ),
            ChunkDraft(
                1,
                "child overview",
                Locator(start_line=4, end_line=8),
                {**common, "chunk_role": "section_parent"},
            ),
            ChunkDraft(
                2,
                "precise child evidence",
                Locator(start_line=5, end_line=6),
                {**common, "chunk_role": "section_child"},
            ),
        )


@pytest.mark.asyncio
async def test_pipeline_builds_stable_versioned_chunks_and_upserts_search() -> None:
    """验证本测试场景的预期行为与边界条件。"""
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


@pytest.mark.asyncio
async def test_pipeline_collapses_duplicate_chunk_ids_before_embedding() -> None:
    """相同逻辑 Chunk 保留首次来源，并且只向量化和索引一次。"""
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    storage = FakeObjectStorage()
    model = _RecordingModel(dimension=8)
    search = FakeSearchEngine()
    documents = DocumentService(repository, storage, max_upload_bytes=1024)
    await documents.create_dataset(
        CreateDatasetCommand("trace", "create-dedup", "Docs", "fake", 8, now, "dataset-2")
    )
    await documents.submit_document(
        SubmitDocumentCommand(
            "trace",
            "submit-dedup",
            "dataset-2",
            "duplicates.txt",
            b"source",
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
        chunker=_DuplicateChunker(),
        model=model,
        search=search,
    )

    chunks = await pipeline.execute(claim)

    assert [chunk.ordinal for chunk in chunks] == [0, 2]
    assert [chunk.metadata["position"] for chunk in chunks] == ["first", "third"]
    assert model.embedded_batches == [("repeated evidence", "unique evidence")]
    assert search.record_count == 2


@pytest.mark.asyncio
async def test_pipeline_resolves_child_and_ancestor_parent_chunk_ids() -> None:
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    storage = FakeObjectStorage()
    model = FakeModelGateway(dimension=8)
    search = FakeSearchEngine()
    documents = DocumentService(repository, storage, max_upload_bytes=1024)
    await documents.create_dataset(
        CreateDatasetCommand("trace", "create-hierarchy", "Docs", "fake", 8, now, "dataset-3")
    )
    await documents.submit_document(
        SubmitDocumentCommand(
            "trace",
            "submit-hierarchy",
            "dataset-3",
            "manual.chm",
            b"source",
            None,
            None,
            "source-router-v7",
            800,
            120,
            "fake",
            now,
        )
    )
    await finalize_once(repository, storage, now, limit=10)
    task = next(iter(repository.tasks.values()))
    claim = await repository.claim_task(task.id, delivery_sequence=1, now=now)
    assert claim is not None

    chunks = await IngestionPipeline(
        storage,
        TextParser(),
        _HierarchyChunker(),
        model,
        search,
    ).execute(claim)

    by_content = {chunk.content_with_weight: chunk for chunk in chunks}
    assert by_content["root overview"].metadata["parent_chunk_id"] == ""
    assert (
        by_content["child overview"].metadata["parent_chunk_id"] == by_content["root overview"].id
    )
    assert (
        by_content["precise child evidence"].metadata["parent_chunk_id"]
        == by_content["child overview"].id
    )
