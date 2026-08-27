"""唯一运行时组合根：在进程边界创建 concrete adapter 并管理其关闭顺序。"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from types import FrameType
from typing import Literal

import httpx
from elasticsearch import AsyncElasticsearch

from rag_mvp.adapters.chunkers.recursive import RecursiveChunker
from rag_mvp.adapters.message_queue.nats_jetstream import NatsJetStreamTaskQueue
from rag_mvp.adapters.metadata.database import create_mysql_engine, create_session_factory
from rag_mvp.adapters.metadata.mysql import MySQLMetadataRepository
from rag_mvp.adapters.model.openai_compatible import OpenAICompatibleModelGateway
from rag_mvp.adapters.parsers.router import SourceParserRouter
from rag_mvp.adapters.search_engine.elasticsearch import ElasticsearchSearchEngine
from rag_mvp.adapters.storage.local import LocalObjectStorage
from rag_mvp.application.cleanup_service import CleanupService
from rag_mvp.application.document_service import DocumentService
from rag_mvp.application.ingestion_service import IngestionService
from rag_mvp.application.job_service import JobService
from rag_mvp.application.retrieval_service import RetrievalService
from rag_mvp.config import Settings
from rag_mvp.ingestion.checkpoints import Failpoint
from rag_mvp.ingestion.failpoints import FileBarrierFailpoint
from rag_mvp.ingestion.pipeline import IngestionPipeline
from rag_mvp.ports.message_queue import TaskQueue
from rag_mvp.ports.metadata import MetadataRepository
from rag_mvp.ports.model import ModelGateway
from rag_mvp.ports.search_engine import SearchEngine
from rag_mvp.ports.storage import ObjectStorage
from rag_mvp.rpc.rag_service import RagService

AsyncCloser = Callable[[], Awaitable[None]]
ProcessRole = Literal["server", "worker", "outbox"]


@dataclass(frozen=True, slots=True)
class ManagedResource[T]:
    """One adapter and its optional async lifecycle callback."""

    value: T
    close: AsyncCloser | None = None


type ResourceFactory[T] = Callable[[Settings], Awaitable[ManagedResource[T]]]


@dataclass(frozen=True, slots=True)
class AdapterFactories:
    """Injectable adapter factories used to enforce process-role boundaries."""

    metadata: ResourceFactory[MetadataRepository]
    storage: ResourceFactory[ObjectStorage]
    search: ResourceFactory[SearchEngine]
    model: ResourceFactory[ModelGateway]
    queue: ResourceFactory[TaskQueue]


@dataclass(slots=True)
class Container:
    """Role-specific service graph with reverse-order idempotent cleanup."""

    settings: Settings
    role: ProcessRole
    rag_service: RagService | None = None
    metadata: MetadataRepository | None = None
    storage: ObjectStorage | None = None
    queue: TaskQueue | None = None
    search: SearchEngine | None = None
    model: ModelGateway | None = None
    ingestion: IngestionService | None = None
    cleanup: CleanupService | None = None
    failpoint: Failpoint | None = None
    _closers: list[AsyncCloser] = field(default_factory=list, init=False, repr=False)
    _closed: bool = field(default=False, init=False)
    _close_count: int = field(default=0, init=False)

    @property
    # 实现 closed 对应的局部职责。
    def closed(self) -> bool:
        return self._closed

    @property
    # 实现 close_count 对应的局部职责。
    def close_count(self) -> int:
        return self._close_count

    # 实现 register 对应的局部职责。
    def register[T](self, resource: ManagedResource[T]) -> T:
        if self._closed:
            raise RuntimeError("cannot register a resource on a closed container")
        if resource.close is not None:
            self._closers.append(resource.close)
        return resource.value

    # 按资源所有权顺序关闭底层连接或句柄。
    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._close_count += 1
        first_error: Exception | None = None
        for close in reversed(self._closers):
            try:
                await close()
            except Exception as error:
                if first_error is None:
                    first_error = error
        self._closers.clear()
        if first_error is not None:
            raise first_error


# 实现 default_adapter_factories 对应的局部职责。
def default_adapter_factories() -> AdapterFactories:
    """Return production factories without creating connections at import time."""

    return AdapterFactories(
        metadata=_metadata_resource,
        storage=_storage_resource,
        search=_search_resource,
        model=_model_resource,
        queue=_queue_resource,
    )


# 构建该方法负责的领域数据或基础设施状态。
async def build_server_container(
    settings: Settings,
    factories: AdapterFactories | None = None,
) -> Container:
    """Build the private gRPC role without a queue consumer."""

    selected = factories or default_adapter_factories()
    container = Container(settings, "server")
    try:
        metadata = container.register(await selected.metadata(settings))
        storage = container.register(await selected.storage(settings))
        search = container.register(await selected.search(settings))
        model = container.register(await selected.model(settings))
        profile = settings.require_embedding_profile()
        container.metadata = metadata
        container.storage = storage
        container.search = search
        container.model = model
        documents = DocumentService(
            metadata,
            storage,
            max_upload_bytes=settings.max_upload_bytes,
            default_tenant_id=settings.default_tenant_id,
            embedding_model=profile.model,
            embedding_dimension=profile.dimension,
        )
        container.rag_service = RagService(
            documents=documents,
            jobs=JobService(metadata, max_user_retries=settings.max_user_retries),
            retrieval=RetrievalService(metadata, search, model),
            parser_version=settings.parser_version,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            embedding_model=profile.model,
        )
        return container
    except BaseException:
        with suppress(Exception):
            await container.close()
        raise


# 构建该方法负责的领域数据或基础设施状态。
async def build_worker_container(
    settings: Settings,
    factories: AdapterFactories | None = None,
) -> Container:
    """Build the sole JetStream consumer and Task executor role."""

    selected = factories or default_adapter_factories()
    container = Container(settings, "worker")
    try:
        metadata = container.register(await selected.metadata(settings))
        storage = container.register(await selected.storage(settings))
        search = container.register(await selected.search(settings))
        model = container.register(await selected.model(settings))
        queue = container.register(await selected.queue(settings))
        failpoint = FileBarrierFailpoint.from_settings(settings)
        container.metadata = metadata
        container.storage = storage
        container.search = search
        container.model = model
        container.queue = queue
        container.failpoint = failpoint
        pipeline = IngestionPipeline(
            storage,
            SourceParserRouter(),
            RecursiveChunker(settings.chunk_size, settings.chunk_overlap),
            model,
            search,
            failpoint=failpoint,
        )
        container.ingestion = IngestionService(metadata, pipeline)
        container.cleanup = CleanupService(metadata, search, storage)
        return container
    except BaseException:
        with suppress(Exception):
            await container.close()
        raise


# 构建该方法负责的领域数据或基础设施状态。
async def build_outbox_container(
    settings: Settings,
    factories: AdapterFactories | None = None,
) -> Container:
    """Build Finalizer, Relay, and staging Sweeper dependencies only."""

    selected = factories or default_adapter_factories()
    container = Container(settings, "outbox")
    try:
        container.metadata = container.register(await selected.metadata(settings))
        container.storage = container.register(await selected.storage(settings))
        container.queue = container.register(await selected.queue(settings))
        container.failpoint = FileBarrierFailpoint.from_settings(settings)
        return container
    except BaseException:
        with suppress(Exception):
            await container.close()
        raise


# 内部辅助：完成 metadata_resource 所需的局部转换或校验。
async def _metadata_resource(settings: Settings) -> ManagedResource[MetadataRepository]:
    engine = create_mysql_engine(settings.mysql_dsn)

    # 按资源所有权顺序关闭底层连接或句柄。
    async def close() -> None:
        await engine.dispose()

    return ManagedResource(
        MySQLMetadataRepository(
            create_session_factory(engine),
            default_tenant_id=settings.default_tenant_id,
        ),
        close,
    )


# 内部辅助：完成 storage_resource 所需的局部转换或校验。
async def _storage_resource(settings: Settings) -> ManagedResource[ObjectStorage]:
    return ManagedResource(LocalObjectStorage(settings.object_root))


# 内部辅助：完成 search_resource 所需的局部转换或校验。
async def _search_resource(settings: Settings) -> ManagedResource[SearchEngine]:
    profile = settings.require_embedding_profile()
    client = AsyncElasticsearch(settings.elasticsearch_url)
    search = ElasticsearchSearchEngine(
        client,
        settings.elasticsearch_index,
        profile.dimension,
    )
    try:
        await search.ensure_index()
    except BaseException:
        await search.close()
        raise
    return ManagedResource(search, search.close)


# 内部辅助：完成 model_resource 所需的局部转换或校验。
async def _model_resource(settings: Settings) -> ManagedResource[ModelGateway]:
    profile = settings.require_embedding_profile()
    client = httpx.AsyncClient(
        headers={"Authorization": f"Bearer {profile.api_key.get_secret_value()}"},
        timeout=profile.timeout_seconds,
    )
    model = OpenAICompatibleModelGateway(
        client,
        profile.endpoint,
        profile.model,
        profile.dimension,
        profile.batch_size,
        profile.max_retries,
    )
    return ManagedResource(model, model.close)


# 内部辅助：完成 queue_resource 所需的局部转换或校验。
async def _queue_resource(settings: Settings) -> ManagedResource[TaskQueue]:
    queue = await NatsJetStreamTaskQueue.connect(
        settings.nats_url,
        settings.nats_stream,
        settings.nats_subject,
        settings.nats_consumer,
        settings.nats_ack_wait_seconds,
        settings.nats_max_deliver,
    )
    return ManagedResource(queue, queue.close)


# 实现 install_shutdown_handlers 对应的局部职责。
def install_shutdown_handlers(stop_event: asyncio.Event) -> None:
    """Set *stop_event* for SIGINT/SIGTERM on Unix and Windows event loops."""

    loop = asyncio.get_running_loop()

    # 实现 request_stop 对应的局部职责。
    def request_stop(_signum: int | None = None, _frame: FrameType | None = None) -> None:
        loop.call_soon_threadsafe(stop_event.set)

    for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(shutdown_signal, stop_event.set)
        except NotImplementedError:
            signal.signal(shutdown_signal, request_stop)
