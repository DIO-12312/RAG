"""Generated-gRPC-only helpers for real Docker E2E tests."""

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
    return f"{prefix}-{uuid4().hex}"


def _result(response: Any) -> Any:
    if response.WhichOneof("outcome") == "result":
        return response.result
    raise AssertionError(f"gRPC business error {response.error.code}: {response.error.message}")


@pytest.fixture
def embedding_runtime() -> EmbeddingRuntime:
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
    target = os.getenv("RAG_GRPC_TARGET", "rag-server:50051")
    channel = grpc.aio.insecure_channel(target)
    try:
        await asyncio.wait_for(channel.channel_ready(), timeout=10)
        yield rag_service_pb2_grpc.RagServiceStub(channel)
    finally:
        await channel.close()


async def create_dataset(stub: Any, runtime: EmbeddingRuntime, case_name: str) -> str:
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
    async def frames() -> AsyncIterator[rag_service_pb2.UploadDocumentRequest]:
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
        await asyncio.sleep(0.25)
    raise AssertionError(f"ingestion job {job_id} did not finish within {deadline_seconds}s")


async def retrieve(stub: Any, dataset_id: str, query: str) -> Any:
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
