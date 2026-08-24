"""Composition-root tests for role-specific real process dependencies."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import pytest

from rag_mvp.bootstrap.container import (
    AdapterFactories,
    ManagedResource,
    build_outbox_container,
    build_server_container,
    build_worker_container,
)
from rag_mvp.config import Environment, Settings


class _Adapter:
    def __init__(self, name: str) -> None:
        self.name = name


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        environment=Environment.TEST,
        grpc_reflection=False,
        mysql_dsn="mysql+asyncmy://test:test@127.0.0.1:3306/rag_test",
        elasticsearch_url="http://127.0.0.1:9200",
        nats_url="nats://127.0.0.1:4222",
        object_root=tmp_path,
        embedding_model_url="https://model.example/v1",
        embedding_model_name="embedding-model",
        embedding_model_api_key="test-key",
        embedding_model_dimension=3,
    )


def _factories(events: list[str]) -> AdapterFactories:
    def factory(name: str) -> Callable[[Settings], Awaitable[ManagedResource[Any]]]:
        async def build(_settings: Settings) -> ManagedResource[Any]:
            events.append(f"build:{name}")

            async def close() -> None:
                events.append(f"close:{name}")

            return ManagedResource(_Adapter(name), close)

        return build

    return AdapterFactories(
        metadata=cast(Any, factory("metadata")),
        storage=cast(Any, factory("storage")),
        search=cast(Any, factory("search")),
        model=cast(Any, factory("model")),
        queue=cast(Any, factory("queue")),
    )


@pytest.mark.asyncio
async def test_role_factories_build_only_allowed_dependencies_and_services(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)

    server_events: list[str] = []
    server = await build_server_container(settings, _factories(server_events))
    assert server.role == "server"
    assert server_events == ["build:metadata", "build:storage", "build:search", "build:model"]
    assert server.rag_service is not None
    assert server.metadata is not None and server.storage is not None
    assert server.search is not None and server.model is not None
    assert server.queue is None and server.ingestion is None and server.cleanup is None

    worker_events: list[str] = []
    worker = await build_worker_container(settings, _factories(worker_events))
    assert worker.role == "worker"
    assert worker_events == [
        "build:metadata",
        "build:storage",
        "build:search",
        "build:model",
        "build:queue",
    ]
    assert worker.rag_service is None
    assert worker.ingestion is not None and worker.cleanup is not None
    assert worker.queue is not None

    outbox_events: list[str] = []
    outbox = await build_outbox_container(settings, _factories(outbox_events))
    assert outbox.role == "outbox"
    assert outbox_events == ["build:metadata", "build:storage", "build:queue"]
    assert outbox.metadata is not None and outbox.storage is not None
    assert outbox.queue is not None
    assert outbox.search is None and outbox.model is None
    assert outbox.ingestion is None and outbox.cleanup is None and outbox.rag_service is None

    await server.close()
    await worker.close()
    await outbox.close()


@pytest.mark.asyncio
async def test_container_close_is_reverse_order_and_idempotent(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    container = await build_server_container(_settings(tmp_path), _factories(events))

    await container.close()
    await container.close()

    assert events == [
        "build:metadata",
        "build:storage",
        "build:search",
        "build:model",
        "close:model",
        "close:search",
        "close:storage",
        "close:metadata",
    ]
    assert container.closed is True
    assert container.close_count == 1
