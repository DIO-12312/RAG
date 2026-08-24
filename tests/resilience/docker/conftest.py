"""Real Compose, Docker control, gRPC, and storage probes for resilience tests."""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

import grpc
import pytest
import pytest_asyncio
from elasticsearch import AsyncElasticsearch
from nats.aio.client import Client as NATS
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from rag_mvp.adapters.metadata.database import create_session_factory
from rag_mvp.adapters.metadata.mysql import MySQLMetadataRepository
from rag_mvp.ingestion.checkpoints import Checkpoint
from rag_mvp.rpc.generated import rag_service_pb2, rag_service_pb2_grpc

UPLOAD_FRAME_BYTES = 64 * 1024


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Keep default local/coverage runs separate from destructive Docker control."""

    if "docker_resilience" in config.option.markexpr:
        return
    unselected = pytest.mark.skip(reason="requires explicit -m docker_resilience selection")
    for item in items:
        if item.get_closest_marker("docker_resilience") is not None:
            item.add_marker(unselected)


def unique_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def grpc_result(response: Any) -> Any:
    if response.WhichOneof("outcome") == "result":
        return response.result
    raise AssertionError(f"gRPC business error {response.error.code}: {response.error.message}")


@dataclass(frozen=True, slots=True)
class EmbeddingRuntime:
    model: str
    dimension: int


@dataclass(frozen=True, slots=True)
class DockerControl:
    worker: str
    outbox: str
    nats: str

    def run(self, *arguments: str, timeout: float = 30) -> str:
        completed = subprocess.run(
            ["docker", *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip()
            raise AssertionError(f"docker {' '.join(arguments)} failed: {message}")
        return completed.stdout.strip()

    def kill(self, container: str) -> None:
        self.run("kill", "--signal=KILL", container)

    def start(self, container: str) -> None:
        self.run("start", container)
        self.wait_running(container)

    def stop(self, container: str) -> None:
        self.run("stop", "--time=1", container)

    def wait_running(self, container: str, timeout: float = 30) -> None:
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            state = self.run("inspect", "--format={{.State.Running}}", container)
            if state == "true":
                return
            import time

            time.sleep(0.1)
        raise AssertionError(f"container did not become running: {container}")

    def wait_healthy(self, container: str, timeout: float = 60) -> None:
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            state = self.run(
                "inspect",
                "--format={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
                container,
            )
            if state in {"healthy", "none"}:
                return
            import time

            time.sleep(0.2)
        raise AssertionError(f"container did not become healthy: {container}")


@dataclass(frozen=True, slots=True)
class BarrierControl:
    root: Path

    def prepare(self, target: Checkpoint | None) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for marker in self.root.glob("*.reached"):
            marker.unlink()
        for marker in self.root.glob("*.release"):
            marker.unlink()
        for checkpoint in Checkpoint:
            if checkpoint is not target:
                self.reached(checkpoint).touch()

    def reached(self, checkpoint: Checkpoint) -> Path:
        return self.root / f"{checkpoint.value}.reached"

    def release(self, checkpoint: Checkpoint) -> Path:
        return self.root / f"{checkpoint.value}.release"

    async def wait_reached(self, checkpoint: Checkpoint, deadline_seconds: float = 120) -> None:
        await wait_until(
            lambda: self.reached(checkpoint).exists(),
            deadline_seconds=deadline_seconds,
        )

    def release_all(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for checkpoint in Checkpoint:
            self.release(checkpoint).touch()


@dataclass(frozen=True, slots=True)
class DatabaseProbe:
    engine: AsyncEngine

    async def one(self, statement: str, **parameters: object) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            result = await connection.execute(text(statement), parameters)
            row = result.mappings().one()
            return dict(row)

    async def scalar(self, statement: str, **parameters: object) -> Any:
        async with self.engine.connect() as connection:
            return await connection.scalar(text(statement), parameters)


@pytest.fixture
def embedding_runtime() -> EmbeddingRuntime:
    model = os.getenv("EMBEDDING_MODEL_NAME", "").strip()
    raw_dimension = os.getenv("EMBEDDING_MODEL_DIMENSION", "").strip()
    if not model or not raw_dimension:
        pytest.fail("Docker resilience requires embedding model name and dimension")
    return EmbeddingRuntime(model, int(raw_dimension))


@pytest_asyncio.fixture
async def rag_stub() -> AsyncIterator[Any]:
    channel = grpc.aio.insecure_channel(os.getenv("RAG_GRPC_TARGET", "rag-server:50051"))
    try:
        await asyncio.wait_for(channel.channel_ready(), timeout=15)
        yield rag_service_pb2_grpc.RagServiceStub(channel)
    finally:
        await channel.close()


@pytest.fixture
def docker_control() -> DockerControl:
    required = {
        "worker": os.getenv("RAG_DOCKER_WORKER_CONTAINER", ""),
        "outbox": os.getenv("RAG_DOCKER_OUTBOX_CONTAINER", ""),
        "nats": os.getenv("RAG_DOCKER_NATS_CONTAINER", ""),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        pytest.fail(f"Docker resilience container names are missing: {', '.join(missing)}")
    control = DockerControl(**required)
    control.run("version", "--format={{.Client.Version}}/{{.Server.Version}}")
    return control


@pytest.fixture
def barrier_control() -> BarrierControl:
    raw_root = os.getenv("RAG_FAILPOINT_ROOT", "").strip()
    if not raw_root:
        pytest.fail("Docker resilience requires RAG_FAILPOINT_ROOT")
    return BarrierControl(Path(raw_root))


@pytest_asyncio.fixture
async def database_probe() -> AsyncIterator[DatabaseProbe]:
    engine = create_async_engine(os.environ["RAG_MYSQL_DSN"], pool_pre_ping=True)
    try:
        yield DatabaseProbe(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def metadata_repository(database_probe: DatabaseProbe) -> MySQLMetadataRepository:
    return MySQLMetadataRepository(
        create_session_factory(database_probe.engine),
        default_tenant_id="default_tenant",
    )


@pytest_asyncio.fixture
async def es_client() -> AsyncIterator[AsyncElasticsearch]:
    client = AsyncElasticsearch(os.environ["RAG_ELASTICSEARCH_URL"])
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture(autouse=True)
def restore_processes_and_disable_barriers(
    docker_control: DockerControl,
    barrier_control: BarrierControl,
) -> Iterator[None]:
    barrier_control.release_all()
    for container in (docker_control.nats, docker_control.outbox, docker_control.worker):
        docker_control.start(container)
    docker_control.wait_healthy(docker_control.nats)
    barrier_control.prepare(None)
    yield
    barrier_control.release_all()
    for container in (docker_control.nats, docker_control.outbox, docker_control.worker):
        docker_control.start(container)


async def wait_until(
    predicate: Callable[[], bool | Awaitable[bool]],
    *,
    deadline_seconds: float = 120,
    interval: float = 0.1,
) -> None:
    deadline = monotonic() + deadline_seconds
    while monotonic() < deadline:
        outcome = predicate()
        if isinstance(outcome, bool):
            matched = outcome
        else:
            matched = await outcome
        if matched:
            return
        await asyncio.sleep(interval)
    raise AssertionError(f"condition did not converge within {deadline_seconds}s")


async def create_dataset(stub: Any, runtime: EmbeddingRuntime, name: str) -> str:
    response = await stub.CreateDataset(
        rag_service_pb2.CreateDatasetRequest(
            context=rag_service_pb2.RequestContext(
                request_id=unique_id("create-request"),
                idempotency_key=unique_id("create-key"),
            ),
            name=f"Docker resilience {name} {uuid4().hex}",
            embedding_model=runtime.model,
            embedding_dimension=runtime.dimension,
            retrieval_config=rag_service_pb2.RetrievalConfig(
                dense_top_k=20,
                sparse_top_k=20,
                rrf_k=60,
                max_context_tokens=4000,
            ),
        ),
        timeout=30,
    )
    return str(grpc_result(response).dataset_id)


async def submit_bytes(
    stub: Any,
    dataset_id: str,
    source_name: str,
    content: bytes,
    *,
    target_document_id: str | None = None,
) -> tuple[str, str, bool]:
    async def frames() -> AsyncIterator[rag_service_pb2.UploadDocumentRequest]:
        header = rag_service_pb2.UploadHeader(
            context=rag_service_pb2.RequestContext(
                request_id=unique_id("submit-request"),
                idempotency_key=unique_id("submit-key"),
            ),
            dataset_id=dataset_id,
            source_name=source_name,
        )
        if target_document_id is not None:
            header.target_document_id = target_document_id
        yield rag_service_pb2.UploadDocumentRequest(header=header)
        for offset in range(0, len(content), UPLOAD_FRAME_BYTES):
            yield rag_service_pb2.UploadDocumentRequest(
                data=content[offset : offset + UPLOAD_FRAME_BYTES]
            )

    result = grpc_result(await stub.SubmitDocument(frames(), timeout=60))
    return str(result.document_id), str(result.job_id), bool(result.reused)


async def get_job(stub: Any, job_id: str) -> Any:
    return grpc_result(
        await stub.GetJob(
            rag_service_pb2.GetJobRequest(
                request_id=unique_id("get-job-request"),
                job_id=job_id,
            ),
            timeout=15,
        )
    )


async def wait_for_job_status(
    stub: Any,
    job_id: str,
    status: int,
    deadline_seconds: float = 180,
) -> Any:
    result: Any = None

    async def matches() -> bool:
        nonlocal result
        result = await get_job(stub, job_id)
        if (
            result.status
            in {
                rag_service_pb2.JOB_STATUS_FAILED,
                rag_service_pb2.JOB_STATUS_CANCELLED,
            }
            and result.status != status
        ):
            raise AssertionError(f"job {job_id} reached unexpected terminal status {result.status}")
        return bool(result.status == status)

    await wait_until(matches, deadline_seconds=deadline_seconds, interval=0.25)
    return result


async def retry_job(stub: Any, job_id: str) -> Any:
    response = await stub.RetryJob(
        rag_service_pb2.RetryJobRequest(
            context=rag_service_pb2.RequestContext(
                request_id=unique_id("retry-request"),
                idempotency_key=unique_id("retry-key"),
            ),
            job_id=job_id,
        ),
        timeout=30,
    )
    return grpc_result(response)


async def delete_document(stub: Any, document_id: str) -> Any:
    response = await stub.DeleteDocument(
        rag_service_pb2.DeleteDocumentRequest(
            context=rag_service_pb2.RequestContext(
                request_id=unique_id("delete-request"),
                idempotency_key=unique_id("delete-key"),
            ),
            document_id=document_id,
        ),
        timeout=30,
    )
    return grpc_result(response)


async def wait_for_queue_drained(deadline_seconds: float = 60) -> None:
    async def drained() -> bool:
        client = NATS()
        try:
            await client.connect(os.environ["RAG_NATS_URL"], connect_timeout=2)
            info = await client.jetstream().consumer_info(
                os.environ["RAG_NATS_STREAM"],
                os.environ["RAG_NATS_CONSUMER"],
            )
            return info.num_pending == 0 and info.num_ack_pending == 0
        except Exception:
            return False
        finally:
            if client.is_connected:
                await client.close()

    await wait_until(drained, deadline_seconds=deadline_seconds, interval=0.25)
