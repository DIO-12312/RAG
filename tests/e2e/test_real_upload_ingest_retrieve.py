"""真实 Compose 下上传、摄取、向量化、索引和检索的端到端验证。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from rag_mvp.rpc.generated import rag_service_pb2
from tests.e2e.conftest import (
    DOCUMENTS,
    EmbeddingRuntime,
    create_dataset,
    retrieve,
    submit_document,
    wait_for_job,
)


@dataclass(frozen=True, slots=True)
class DocumentCase:
    source_name: str
    query: str
    expected_text: str
    source_type: str
    start_line: int | None = None
    end_line: int | None = None
    symbol: str | None = None
    language: str | None = None
    page_number: int | None = None
    section: str | None = None


CASES = (
    DocumentCase(
        "knowledge.txt",
        "Which checksum does the Orchid Harbor recovery protocol use?",
        "cobalt checksum",
        "text",
        start_line=1,
        end_line=3,
    ),
    DocumentCase(
        "guide.md",
        "What is the Aurora Cache Policy refresh threshold?",
        "seventy-three minutes",
        "markdown",
        start_line=3,
        end_line=5,
        section="Aurora Cache Policy",
    ),
    DocumentCase(
        "sample.py",
        "What multiplier does calculate_nebula_window use?",
        "samples * 19",
        "code",
        start_line=1,
        end_line=3,
        symbol="calculate_nebula_window",
        language="python",
    ),
    DocumentCase(
        "manual.pdf",
        "How often is the Quartz Beacon calibration interval?",
        "thirty-seven days",
        "pdf",
        page_number=1,
    ),
)


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case.source_name)
async def test_real_upload_ingest_and_hybrid_retrieve_preserves_provenance(
    rag_stub: object,
    embedding_runtime: EmbeddingRuntime,
    case: DocumentCase,
) -> None:
    """验证真实 Docker 链路摄取后可混合召回并保留来源定位。"""
    dataset_id = await create_dataset(rag_stub, embedding_runtime, case.source_name)
    document_id, job_id = await submit_document(
        rag_stub,
        dataset_id,
        DOCUMENTS / case.source_name,
    )

    job = await wait_for_job(rag_stub, job_id)
    result = await retrieve(rag_stub, dataset_id, case.query)

    assert job.document_id == document_id
    assert job.status == rag_service_pb2.JOB_STATUS_SUCCEEDED
    assert job.task_status == rag_service_pb2.TASK_STATUS_SUCCEEDED
    assert job.progress == pytest.approx(1.0)
    evidence = next(item for item in result.evidence if item.document_id == document_id)
    assert evidence.source_name == case.source_name
    assert case.expected_text in evidence.content_with_weight
    assert evidence.index_version == 1
    assert evidence.chunk_id
    assert evidence.metadata["source_type"] == case.source_type
    assert evidence.scores.HasField("dense_score")
    assert evidence.scores.HasField("sparse_score")
    assert evidence.scores.HasField("fusion_score")
    assert evidence.scores.fusion_score > 0

    if case.start_line is not None:
        assert evidence.locator.start_line == case.start_line
    if case.end_line is not None:
        assert evidence.locator.end_line == case.end_line
    if case.symbol is not None:
        assert evidence.locator.symbol == case.symbol
    if case.language is not None:
        assert evidence.locator.language == case.language
    if case.page_number is not None:
        assert evidence.locator.page_number == case.page_number
        assert evidence.locator.start_line >= 1
        assert evidence.locator.end_line >= evidence.locator.start_line
    if case.section is not None:
        assert evidence.metadata["section"] == case.section
