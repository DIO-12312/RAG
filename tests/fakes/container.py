"""Mock Functional composition using real use cases and test-only infrastructure ports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rag_mvp.adapters.chunkers.recursive import RecursiveChunker
from rag_mvp.adapters.parsers.text import TextParser
from rag_mvp.adapters.storage.local import LocalObjectStorage
from rag_mvp.application.document_service import DocumentService
from rag_mvp.application.ingestion_service import IngestionService
from rag_mvp.application.job_service import JobService
from rag_mvp.application.retrieval_service import RetrievalService
from rag_mvp.ingestion.pipeline import IngestionPipeline
from rag_mvp.ingestion.worker import worker_once
from rag_mvp.outbox.finalizer import finalize_once
from rag_mvp.outbox.relay import relay_once
from rag_mvp.rpc.rag_service import RagService
from tests.fakes.metadata import FakeMetadataRepository
from tests.fakes.model import FakeModelGateway
from tests.fakes.search_engine import FakeSearchEngine
from tests.fakes.task_queue import FakeTaskQueue


@dataclass(slots=True)
class MockFunctionalHarness:
    now: datetime
    metadata: FakeMetadataRepository
    storage: LocalObjectStorage
    queue: FakeTaskQueue
    model: FakeModelGateway
    search: FakeSearchEngine
    ingestion: IngestionService
    rpc: RagService

    @classmethod
    def build(cls, object_root: Path, now: datetime) -> MockFunctionalHarness:
        metadata = FakeMetadataRepository()
        storage = LocalObjectStorage(object_root)
        queue = FakeTaskQueue()
        model = FakeModelGateway(8)
        search = FakeSearchEngine()
        documents = DocumentService(metadata, storage, max_upload_bytes=4 * 1024 * 1024)
        ingestion = IngestionService(
            metadata,
            IngestionPipeline(
                storage,
                TextParser(),
                RecursiveChunker(800, 120),
                model,
                search,
            ),
        )
        rpc = RagService(
            documents=documents,
            jobs=JobService(metadata),
            retrieval=RetrievalService(metadata, search, model),
            now=lambda: now,
        )
        return cls(now, metadata, storage, queue, model, search, ingestion, rpc)

    async def run_ingestion_once(self) -> None:
        await finalize_once(self.metadata, self.storage, self.now, limit=100)
        await relay_once(self.metadata, self.queue, self.now, limit=100)
        await worker_once(
            self.queue,
            self.metadata,
            self.ingestion,
            "functional-worker",
            self.now,
        )
