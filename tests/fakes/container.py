"""用真实用例与测试伪端口装配的 Mock Functional 运行容器。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rag_mvp.adapters.chunkers.recursive import RecursiveChunker
from rag_mvp.adapters.parsers.router import SourceParserRouter
from rag_mvp.adapters.storage.local import LocalObjectStorage
from rag_mvp.application.cleanup_service import CleanupService
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
    cleanup: CleanupService
    rpc: RagService

    @classmethod
    def build(cls, object_root: Path, now: datetime) -> MockFunctionalHarness:
        """构造可用于功能测试的完整模拟容器。"""
        metadata = FakeMetadataRepository()
        storage = LocalObjectStorage(object_root)
        queue = FakeTaskQueue()
        model = FakeModelGateway(8)
        search = FakeSearchEngine()
        documents = DocumentService(
            metadata,
            storage,
            max_upload_bytes=4 * 1024 * 1024,
            default_tenant_id="default_tenant",
            embedding_model="fake",
            embedding_dimension=8,
        )
        ingestion = IngestionService(
            metadata,
            IngestionPipeline(
                storage,
                SourceParserRouter(),
                RecursiveChunker(800, 120),
                model,
                search,
            ),
        )
        cleanup = CleanupService(metadata, search, storage)
        rpc = RagService(
            documents=documents,
            jobs=JobService(metadata),
            retrieval=RetrievalService(metadata, search, model),
            now=lambda: now,
        )
        return cls(now, metadata, storage, queue, model, search, ingestion, cleanup, rpc)

    async def run_ingestion_once(self) -> None:
        """驱动一次 Outbox、队列和 Worker 的完整摄取循环。"""
        await finalize_once(self.metadata, self.storage, self.now, limit=100)
        await relay_once(self.metadata, self.queue, self.now, limit=100)
        await worker_once(
            self.queue,
            self.metadata,
            self.ingestion,
            "functional-worker",
            self.now,
            cleanup=self.cleanup,
        )
