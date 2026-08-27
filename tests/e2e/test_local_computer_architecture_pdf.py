"""使用本地《计组复习》PDF 模拟真实用户全链路摄取与检索。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from rag_mvp.rpc.generated import rag_service_pb2
from tests.e2e.conftest import (
    EmbeddingRuntime,
    create_dataset,
    retrieve,
    submit_document,
    wait_for_job,
)

LOCAL_PDF_ENV = "RAG_E2E_PDF_PATH"
DEFAULT_LOCAL_PDF = Path(__file__).resolve().parents[1] / "object" / "计组复习.pdf"


@pytest.fixture
def local_computer_architecture_pdf() -> Path:
    """定位用户本机的可选《计组复习》PDF fixture。"""
    """Resolve the untracked local PDF without making it a CI requirement."""
    configured_path = os.getenv(LOCAL_PDF_ENV, "").strip()
    source = Path(configured_path).expanduser() if configured_path else DEFAULT_LOCAL_PDF
    if not source.is_file():
        pytest.skip(
            "local real-user PDF is unavailable; place it at "
            f"{DEFAULT_LOCAL_PDF} or set {LOCAL_PDF_ENV} to a container-visible path"
        )
    return source


def _document_evidence(result: Any, document_id: str) -> list[Any]:
    """仅保留指定文档的 evidence，隔离共享集群中的其他数据。"""
    return [item for item in result.evidence if item.document_id == document_id]


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_local_user_uploads_review_pdf_and_retrieves_distant_topics(
    local_computer_architecture_pdf: Path,
    rag_stub: object,
    embedding_runtime: EmbeddingRuntime,
) -> None:
    """模拟用户上传真实复习 PDF，并检索跨页的两个主题。"""
    dataset_id = await create_dataset(
        rag_stub,
        embedding_runtime,
        "local-computer-architecture-pdf",
    )
    document_id, job_id = await submit_document(
        rag_stub,
        dataset_id,
        local_computer_architecture_pdf,
    )

    job = await wait_for_job(rag_stub, job_id, deadline_seconds=600)

    assert job.document_id == document_id
    assert job.status == rag_service_pb2.JOB_STATUS_SUCCEEDED
    assert job.task_status == rag_service_pb2.TASK_STATUS_SUCCEEDED
    assert job.progress == pytest.approx(1.0)

    basic_result = await retrieve(
        stub=rag_stub, dataset_id=dataset_id, query="计算机有哪些基本功能？"
    )
    basic_evidence = _document_evidence(basic_result, document_id)
    assert basic_evidence
    basic_match = next(
        item
        for item in basic_evidence
        if "数据处理" in item.content_with_weight and "过程控制" in item.content_with_weight
    )
    assert basic_match.source_name == "计组复习.pdf"
    assert basic_match.locator.page_number == 5
    assert basic_match.metadata["source_type"] == "pdf"
    assert basic_match.chunk_id
    assert basic_match.index_version == 1
    assert basic_match.scores.HasField("dense_score")
    assert basic_match.scores.HasField("sparse_score")
    assert basic_match.scores.HasField("fusion_score")
    assert basic_match.scores.fusion_score > 0

    dma_result = await retrieve(
        stub=rag_stub,
        dataset_id=dataset_id,
        query="DMA 有哪三种传送方式？",
    )
    dma_evidence = _document_evidence(dma_result, document_id)
    assert dma_evidence
    dma_context = "\n".join(item.content_with_weight for item in dma_evidence)
    assert "暂停CPU访问方式" in dma_context
    assert "周期挪用（窃取）方式" in dma_context
    assert "与CPU交替访问方式" in dma_context
    assert {42, 43}.issubset({item.locator.page_number for item in dma_evidence})
    assert all(item.source_name == "计组复习.pdf" for item in dma_evidence)
    assert all(item.metadata["source_type"] == "pdf" for item in dma_evidence)
