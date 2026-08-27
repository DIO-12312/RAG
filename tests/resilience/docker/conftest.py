"""韧性测试使用的真实 Compose、Docker 控制、gRPC 与存储探针。"""

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
    """将破坏性 Docker 韧性测试从默认本地/覆盖率运行中排除。"""

    if "docker_resilience" in config.option.markexpr:
        return
    unselected = pytest.mark.skip(reason="requires explicit -m docker_resilience selection")
    for item in items:
        if item.get_closest_marker("docker_resilience") is not None:
            item.add_marker(unselected)


def unique_id(prefix: str) -> str:
    """生成真实 Docker 测试的隔离资源名称。"""
    return f"{prefix}-{uuid4().hex}"


def grpc_result(response: Any) -> Any:
    """解包 gRPC 成功结果，业务错误直接转为断言。"""
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
        """运行 docker compose 控制命令并返回标准输出。"""
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
        """强杀容器，模拟进程突然断电。"""
        self.run("kill", "--signal=KILL", container)

    def start(self, container: str) -> None:
        """重新启动指定容器，触发服务恢复。"""
        self.run("start", container)
        self.wait_running(container)

    def stop(self, container: str) -> None:
        """正常停止指定容器，用于依赖不可用场景。"""
        self.run("stop", "--time=1", container)

    def wait_running(self, container: str, timeout: float = 30) -> None:
        """轮询容器运行状态，避免恢复测试发生竞态。"""
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            state = self.run("inspect", "--format={{.State.Running}}", container)
            if state == "true":
                return
            import time

            time.sleep(0.1)
        raise AssertionError(f"container did not become running: {container}")

    def wait_healthy(self, container: str, timeout: float = 60) -> None:
        """等待健康检查通过后再继续真实故障恢复断言。"""
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
        """设置 Worker 应阻塞的检查点，制造可重复的竞态窗口。"""
        self.root.mkdir(parents=True, exist_ok=True)
        for marker in self.root.glob("*.reached"):
            marker.unlink()
        for marker in self.root.glob("*.release"):
            marker.unlink()
        for checkpoint in Checkpoint:
            if checkpoint is not target:
                self.reached(checkpoint).touch()

    def reached(self, checkpoint: Checkpoint) -> Path:
        """返回指定检查点的到达标记文件。"""
        return self.root / f"{checkpoint.value}.reached"

    def release(self, checkpoint: Checkpoint) -> Path:
        """返回释放指定检查点阻塞的标记文件。"""
        return self.root / f"{checkpoint.value}.release"

    async def wait_reached(self, checkpoint: Checkpoint, deadline_seconds: float = 120) -> None:
        """异步等待 Worker 抵达故障屏障检查点。"""
        await wait_until(
            lambda: self.reached(checkpoint).exists(),
            deadline_seconds=deadline_seconds,
        )

    def release_all(self) -> None:
        """释放全部屏障，确保测试清理不会遗留阻塞 Worker。"""
        self.root.mkdir(parents=True, exist_ok=True)
        for checkpoint in Checkpoint:
            self.release(checkpoint).touch()


@dataclass(frozen=True, slots=True)
class DatabaseProbe:
    engine: AsyncEngine

    async def one(self, statement: str, **parameters: object) -> dict[str, Any]:
        """执行查询并返回单行字典，供真实 MySQL 状态断言使用。"""
        async with self.engine.connect() as connection:
            result = await connection.execute(text(statement), parameters)
            row = result.mappings().one()
            return dict(row)

    async def scalar(self, statement: str, **parameters: object) -> Any:
        """执行标量 SQL 查询，简化行数和状态轮询。"""
        async with self.engine.connect() as connection:
            return await connection.scalar(text(statement), parameters)


@pytest.fixture
def embedding_runtime() -> EmbeddingRuntime:
    """读取真实 Embedding 模型配置，保证 E2E 与生产调用一致。"""
    model = os.getenv("EMBEDDING_MODEL_NAME", "").strip()
    raw_dimension = os.getenv("EMBEDDING_MODEL_DIMENSION", "").strip()
    if not model or not raw_dimension:
        pytest.fail("Docker resilience requires embedding model name and dimension")
    return EmbeddingRuntime(model, int(raw_dimension))


@pytest_asyncio.fixture
async def rag_stub() -> AsyncIterator[Any]:
    """建立直连 Docker RAG 服务的临时 gRPC 客户端。"""
    channel = grpc.aio.insecure_channel(os.getenv("RAG_GRPC_TARGET", "rag-server:50051"))
    try:
        await asyncio.wait_for(channel.channel_ready(), timeout=15)
        yield rag_service_pb2_grpc.RagServiceStub(channel)
    finally:
        await channel.close()


@pytest.fixture
def docker_control() -> DockerControl:
    """提供 Docker Compose 控制器，供强杀与重启测试使用。"""
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
    """提供文件屏障控制器，精确暂停 Worker 生命周期。"""
    raw_root = os.getenv("RAG_FAILPOINT_ROOT", "").strip()
    if not raw_root:
        pytest.fail("Docker resilience requires RAG_FAILPOINT_ROOT")
    return BarrierControl(Path(raw_root))


@pytest_asyncio.fixture
async def database_probe() -> AsyncIterator[DatabaseProbe]:
    """提供真实 MySQL 探针以核验服务外部状态。"""
    engine = create_async_engine(os.environ["RAG_MYSQL_DSN"], pool_pre_ping=True)
    try:
        yield DatabaseProbe(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def metadata_repository(database_probe: DatabaseProbe) -> MySQLMetadataRepository:
    """按探针连接创建 metadata repository，用于辅助查询。"""
    return MySQLMetadataRepository(
        create_session_factory(database_probe.engine),
        default_tenant_id="default_tenant",
    )


@pytest_asyncio.fixture
async def es_client() -> AsyncIterator[AsyncElasticsearch]:
    """创建真实 Elasticsearch 客户端，并在测试后关闭。"""
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
    """每个用例前后恢复容器与屏障，隔离真实故障注入的副作用。"""
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
    """无论断言结果如何都恢复容器并释放屏障，避免污染后续测试。"""
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
    """经 gRPC 创建携带真实 Embedding 配置的隔离数据集。"""
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
    """以流式 gRPC 提交字节并返回文档、Job 与去重复用结果。"""

    async def frames() -> AsyncIterator[rag_service_pb2.UploadDocumentRequest]:
        """将上传头和正文按协议帧大小拆分为请求流。"""
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
    """查询 Job 的最新服务端状态。"""
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
    """轮询 Job，直到目标状态或意外终态出现。"""
    result: Any = None

    async def matches() -> bool:
        """检查当前 Job 状态，并在错误终态时立即中止等待。"""
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
    """经 gRPC 请求为可重试 Job 创建或复用子 Job。"""
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
    """经 gRPC 触发文档逻辑删除及其异步清理 Job。"""
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
    """等待 JetStream consumer 不再存在待处理或待确认消息。"""

    async def drained() -> bool:
        """查询 JetStream 消费者的待投递与待确认计数。"""
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
