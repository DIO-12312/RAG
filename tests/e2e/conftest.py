"""真实 Docker 端到端测试共用的 generated-gRPC 调用与探针。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

import grpc
import pytest
import pytest_asyncio

from rag_mvp.rpc.generated import rag_service_pb2, rag_service_pb2_grpc

DOCUMENTS = Path(__file__).resolve().parents[1] / "fixtures" / "documents"
UPLOAD_FRAME_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class EmbeddingRuntime:
    model: str
    dimension: int


def unique_id(prefix: str) -> str:
    """生成每个真实 E2E 资源独占的名称，避免并发测试互相污染。"""
    return f"{prefix}-{uuid4().hex}"


def _result(response: Any) -> Any:
    """解包统一 gRPC 成功结果；业务错误立即转成断言失败。"""
    if response.WhichOneof("outcome") == "result":
        return response.result
    raise AssertionError(f"gRPC business error {response.error.code}: {response.error.message}")


@pytest.fixture
def embedding_runtime() -> EmbeddingRuntime:
    """从环境读取真实 Embedding 模型及其声明向量维度。"""
    model = os.getenv("EMBEDDING_MODEL_NAME", "").strip()
    raw_dimension = os.getenv("EMBEDDING_MODEL_DIMENSION", "").strip()
    if not model or not raw_dimension:
        pytest.fail("real E2E requires embedding model name and dimension")
    try:
        dimension = int(raw_dimension)
    except ValueError:
        pytest.fail("EMBEDDING_MODEL_DIMENSION must be an integer")
    if dimension < 1:
        pytest.fail("EMBEDDING_MODEL_DIMENSION must be positive")
    return EmbeddingRuntime(model, dimension)


@pytest_asyncio.fixture
async def rag_stub() -> AsyncIterator[Any]:
    """建立到 Docker 中 RAG gRPC 服务的临时客户端连接。"""
    target = os.getenv("RAG_GRPC_TARGET", "rag-server:50051")
    channel = grpc.aio.insecure_channel(target)
    try:
        await asyncio.wait_for(channel.channel_ready(), timeout=10)
        yield rag_service_pb2_grpc.RagServiceStub(channel)  # type: ignore[no-untyped-call]
    finally:
        await channel.close()


async def create_dataset(stub: Any, runtime: EmbeddingRuntime, case_name: str) -> str:
    """通过真实 gRPC 创建测试专用数据集并返回其 ID。"""
    response = await stub.CreateDataset(
        rag_service_pb2.CreateDatasetRequest(
            context=rag_service_pb2.RequestContext(
                request_id=unique_id("create-request"),
                idempotency_key=unique_id("create-key"),
            ),
            name=f"E2E {case_name} {uuid4().hex}",
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
    return str(_result(response).dataset_id)


async def submit_document(stub: Any, dataset_id: str, source: Path) -> tuple[str, str]:
    """将本地文件按固定帧大小流式上传，返回文档和摄取任务 ID。"""

    async def frames() -> AsyncIterator[rag_service_pb2.UploadDocumentRequest]:
        """先发送上传头，再以受控大小发送二进制帧。"""
        yield rag_service_pb2.UploadDocumentRequest(
            header=rag_service_pb2.UploadHeader(
                context=rag_service_pb2.RequestContext(
                    request_id=unique_id("submit-request"),
                    idempotency_key=unique_id("submit-key"),
                ),
                dataset_id=dataset_id,
                source_name=source.name,
            )
        )
        with source.open("rb") as stream:
            while data := stream.read(UPLOAD_FRAME_BYTES):
                yield rag_service_pb2.UploadDocumentRequest(data=data)

    response = await stub.SubmitDocument(frames(), timeout=60)
    result = _result(response)
    return str(result.document_id), str(result.job_id)


async def wait_for_job(stub: Any, job_id: str, deadline_seconds: float = 240) -> Any:
    """轮询摄取 Job，成功返回，失败或超时则给出明确原因。"""
    deadline = monotonic() + deadline_seconds
    while monotonic() < deadline:
        response = await stub.GetJob(
            rag_service_pb2.GetJobRequest(
                request_id=unique_id("job-request"),
                job_id=job_id,
            ),
            timeout=15,
        )
        result = _result(response)
        if result.status == rag_service_pb2.JOB_STATUS_SUCCEEDED:
            return result
        if result.status in {
            rag_service_pb2.JOB_STATUS_FAILED,
            rag_service_pb2.JOB_STATUS_CANCELLED,
        }:
            failure = result.failure if result.HasField("failure") else None
            code = failure.code if failure is not None else "NO_FAILURE_CODE"
            message = failure.message if failure is not None else "job reached a terminal state"
            raise AssertionError(f"ingestion job ended with {code}: {message}")
        # 避免紧密轮询压垮真实服务，同时保持 E2E 的反馈速度。
        await asyncio.sleep(0.25)
    raise AssertionError(f"ingestion job {job_id} did not finish within {deadline_seconds}s")


async def delete_dataset(stub: Any, dataset_id: str) -> str:
    """提交数据集删除请求并返回异步清理 Job ID。"""
    response = await stub.DeleteDataset(
        rag_service_pb2.DeleteDatasetRequest(
            context=rag_service_pb2.RequestContext(
                request_id=unique_id("delete-dataset-request"),
                idempotency_key=unique_id("delete-dataset-key"),
            ),
            dataset_id=dataset_id,
        ),
        timeout=30,
    )
    return str(_result(response).job_id)


async def wait_for_dataset_purged(
    stub: Any,
    job_id: str,
    deadline_seconds: float = 240,
) -> None:
    """等待数据集清理聚合被彻底删除；删除完成后 Job 会查询不到。"""
    deadline = monotonic() + deadline_seconds
    while monotonic() < deadline:
        response = await stub.GetJob(
            rag_service_pb2.GetJobRequest(
                request_id=unique_id("dataset-purge-request"),
                job_id=job_id,
            ),
            timeout=15,
        )
        if response.WhichOneof("outcome") == "error":
            if response.error.code == "JOB_NOT_FOUND":
                return
            raise AssertionError(
                f"dataset deletion job {job_id} returned "
                f"{response.error.code}: {response.error.message}"
            )
        result = response.result
        if result.status in {
            rag_service_pb2.JOB_STATUS_FAILED,
            rag_service_pb2.JOB_STATUS_CANCELLED,
        }:
            failure = result.failure if result.HasField("failure") else None
            code = failure.code if failure is not None else "NO_FAILURE_CODE"
            message = failure.message if failure is not None else "deletion reached terminal state"
            raise AssertionError(f"dataset deletion job {job_id} ended with {code}: {message}")
        # 物理清理包含消息投递和存储删除，需要短暂让出轮询周期。
        await asyncio.sleep(0.25)
    raise AssertionError(f"dataset deletion job {job_id} was not purged within {deadline_seconds}s")


async def retrieve(stub: Any, dataset_id: str, query: str) -> Any:
    """调用真实检索接口，使用与质量测试一致的召回参数。"""
    response = await stub.Retrieve(
        rag_service_pb2.RetrieveRequest(
            request_id=unique_id("retrieve-request"),
            dataset_id=dataset_id,
            query=query,
            top_k=6,
            max_context_tokens=4000,
        ),
        timeout=60,
    )
    return _result(response)
