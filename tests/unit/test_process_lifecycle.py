from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Awaitable
from pathlib import Path
from typing import cast

import grpc
import pytest

from rag_mvp.bootstrap.container import build_container
from rag_mvp.config import Environment, Settings
from rag_mvp.ingestion.worker import run_worker
from rag_mvp.outbox.main import run_outbox
from rag_mvp.rpc.generated import rag_service_pb2
from rag_mvp.rpc.rag_service import RagService
from rag_mvp.rpc.server import serve


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        environment=Environment.TEST,
        grpc_host="127.0.0.1",
        grpc_port=50052,
        grpc_reflection=False,
        mysql_dsn="mysql+asyncmy://test:test@127.0.0.1:3306/rag_test",
        elasticsearch_url="http://127.0.0.1:9200",
        nats_url="nats://127.0.0.1:4222",
        object_root=tmp_path,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("runner", [run_worker, run_outbox])
async def test_empty_background_process_stops_without_external_connections(
    tmp_path: Path,
    runner: object,
) -> None:
    settings = _settings(tmp_path)
    container = build_container(settings)
    stop_event = asyncio.Event()
    typed_runner = cast(
        "object",
        runner,
    )

    task = asyncio.create_task(
        cast(
            "Awaitable[None]",
            typed_runner(settings, container, stop_event),  # type: ignore[operator]
        )
    )
    await asyncio.sleep(0)
    stop_event.set()
    await asyncio.wait_for(task, timeout=1)

    assert container.closed is False
    await container.close()
    await container.close()
    assert container.closed is True
    assert container.close_count == 1


@pytest.mark.asyncio
async def test_grpc_server_starts_and_stops_cleanly(tmp_path: Path) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    settings = _settings(tmp_path).model_copy(update={"grpc_port": port})
    container = build_container(settings)
    stop_event = asyncio.Event()

    task = asyncio.create_task(serve(settings, container, stop_event))
    await asyncio.sleep(0.05)
    stop_event.set()
    await asyncio.wait_for(task, timeout=1)
    await container.close()

    assert container.closed is True


async def _empty_upload() -> AsyncIterator[rag_service_pb2.UploadDocumentRequest]:
    if False:
        yield rag_service_pb2.UploadDocumentRequest()


@pytest.mark.asyncio
async def test_all_unopened_rpc_methods_return_feature_not_available() -> None:
    service = RagService()
    context = cast("grpc.aio.ServicerContext", None)
    calls: list[Awaitable[object]] = [
        service.CreateDataset(rag_service_pb2.CreateDatasetRequest(), context),
        service.SubmitDocument(_empty_upload(), context),
        service.GetJob(rag_service_pb2.GetJobRequest(request_id="request-1"), context),
        service.RetryJob(rag_service_pb2.RetryJobRequest(), context),
        service.CancelJob(rag_service_pb2.CancelJobRequest(), context),
        service.Retrieve(rag_service_pb2.RetrieveRequest(request_id="request-1"), context),
        service.DeleteDocument(rag_service_pb2.DeleteDocumentRequest(), context),
    ]

    for call in calls:
        response = await call
        assert response.WhichOneof("outcome") == "error"  # type: ignore[attr-defined]
        assert response.error.code == "FEATURE_NOT_AVAILABLE"  # type: ignore[attr-defined]
        assert response.error.retryable is False  # type: ignore[attr-defined]
