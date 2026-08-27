"""针对本地《计组复习》PDF 的真实 50 问检索质量门禁。"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from rag_mvp.adapters.model.openai_compatible import OpenAICompatibleModelGateway
from rag_mvp.rpc.generated import rag_service_pb2
from tests.e2e.conftest import (
    EmbeddingRuntime,
    create_dataset,
    delete_dataset,
    retrieve,
    submit_document,
    wait_for_dataset_purged,
    wait_for_job,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_VARIANT_ENV = "EVAL_FIXTURE"
FIXTURE_VARIANT_PATHS = {
    "original": FIXTURE_DIR / "computer_architecture_knowledge_original.json",
    "rephrased": FIXTURE_DIR / "computer_architecture_knowledge.json",
}


def _fixture_path_for_variant(variant: str) -> Path:
    """根据原始或改写查询变体返回固定评测题集路径。"""
    try:
        return FIXTURE_VARIANT_PATHS[variant]
    except KeyError as exc:
        supported = ", ".join(sorted(FIXTURE_VARIANT_PATHS))
        raise ValueError(f"unsupported {FIXTURE_VARIANT_ENV}={variant!r}; use {supported}") from exc


FIXTURE_VARIANT = os.getenv(FIXTURE_VARIANT_ENV, "rephrased").strip().lower()
FIXTURE_PATH = _fixture_path_for_variant(FIXTURE_VARIANT)
LOG_DIR = Path(__file__).parent / "log"
LOCAL_PDF_ENV = "RAG_E2E_PDF_PATH"
DEFAULT_LOCAL_PDF = Path(__file__).resolve().parents[1] / "object" / "计组复习.pdf"
EXPECTED_CHAPTER_COUNTS = {
    "第一章 计算机系统概论": 7,
    "第四章 指令系统": 9,
    "第五章 中央处理器": 13,
    "第六章 总线": 8,
    "第七章 输入/输出系统": 13,
}
EXPECTED_CHAPTER_PAGES = {
    "第一章 计算机系统概论": frozenset({1, 5, 6, 7}),
    "第四章 指令系统": frozenset(range(8, 13)),
    "第五章 中央处理器": frozenset(range(13, 28)),
    "第六章 总线": frozenset(range(27, 33)),
    "第七章 输入/输出系统": frozenset(range(33, 45)),
}
RECALL_AT_6_THRESHOLD = 0.80
MRR_AT_6_THRESHOLD = 0.65
TOP1_PAGE_HIT_THRESHOLD = 0.60
ANSWER_COVERAGE_THRESHOLD = 0.70


class _GetJobStub:
    def __init__(self, responses: list[Any]) -> None:
        """保存按顺序返回的伪 Job 查询响应。"""
        self.responses = responses

    async def GetJob(self, request: Any, **kwargs: Any) -> Any:
        """模拟 gRPC GetJob，逐个弹出预置响应。"""
        del request, kwargs
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_wait_for_dataset_purged_accepts_job_not_found() -> None:
    """数据集聚合已物理删除后，Job 不存在应视为清理成功。"""
    stub = _GetJobStub(
        [
            rag_service_pb2.GetJobResponse(
                error=rag_service_pb2.BusinessError(
                    code="JOB_NOT_FOUND", message="gone", request_id="request"
                )
            )
        ]
    )

    await wait_for_dataset_purged(stub, "delete-job", deadline_seconds=1)


@pytest.mark.parametrize(
    "status",
    (rag_service_pb2.JOB_STATUS_FAILED, rag_service_pb2.JOB_STATUS_CANCELLED),
)
@pytest.mark.asyncio
async def test_wait_for_dataset_purged_rejects_terminal_failure(status: Any) -> None:
    """删除 Job 以失败或取消终结时，等待函数必须明确报错。"""
    stub = _GetJobStub(
        [
            rag_service_pb2.GetJobResponse(
                result=rag_service_pb2.JobResult(job_id="delete-job", status=status)
            )
        ]
    )

    with pytest.raises(AssertionError, match="delete-job"):
        await wait_for_dataset_purged(stub, "delete-job", deadline_seconds=1)


@pytest.mark.asyncio
async def test_wait_for_dataset_purged_timeout_names_deletion_job() -> None:
    """清理超时时错误信息必须包含待删除 Job ID。"""
    with pytest.raises(AssertionError, match="delete-job"):
        await wait_for_dataset_purged(_GetJobStub([]), "delete-job", deadline_seconds=0)


@dataclass(frozen=True, slots=True)
class KnowledgeCase:
    id: str
    chapter: str
    query: str
    pages: tuple[int, ...]
    answer: str
    required_phrases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    case_id: str
    hit: bool
    reciprocal_rank: float
    top1_page_hit: bool
    answer_covered: bool
    retrieved_pages: tuple[int | None, ...]
    missing_phrases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QualityMetrics:
    recall_at_6: float
    mrr_at_6: float
    top1_page_hit: float
    answer_coverage: float


def _optional_score(scores: Any, name: str) -> float | None:
    """安全提取 protobuf optional 分数字段，未设置则返回 None。"""
    return getattr(scores, name) if scores.HasField(name) else None


def _case_log_record(
    case: KnowledgeCase,
    embedding: tuple[float, ...],
    result: Any,
    outcome: CaseOutcome,
) -> dict[str, Any]:
    """把单题召回、向量和质量结果规范化为可追溯日志记录。"""
    evidence = tuple(result.evidence[:6])

    return {
        "id": case.id,
        "query": case.query,
        "expected_pages": list(case.pages),
        "embedding": list(embedding[:20]),
        "top_k": [
            {
                "rank": rank,
                "chunk_id": item.chunk_id,
                "document_id": item.document_id,
                "index_version": item.index_version,
                "page_number": item.locator.page_number,
                "source_name": item.source_name,
                "scores": {
                    "dense_score": _optional_score(item.scores, "dense_score"),
                    "sparse_score": _optional_score(item.scores, "sparse_score"),
                    "fusion_score": _optional_score(item.scores, "fusion_score"),
                    "rerank_score": _optional_score(item.scores, "rerank_score"),
                },
                "content_with_weight": item.content_with_weight,
            }
            for rank, item in enumerate(evidence, start=1)
        ],
        "metrics": {
            "hit": outcome.hit,
            "reciprocal_rank": outcome.reciprocal_rank,
            "top1_page_hit": outcome.top1_page_hit,
            "answer_covered": outcome.answer_covered,
            "retrieved_pages": list(outcome.retrieved_pages),
            "missing_phrases": list(outcome.missing_phrases),
        },
    }


def _write_run_log(
    cases: tuple[KnowledgeCase, ...],
    embeddings: dict[str, tuple[float, ...]],
    results: dict[str, Any],
    outcomes: tuple[CaseOutcome, ...],
    metrics: QualityMetrics,
    *,
    pdf_path: Path,
) -> Path:
    """将一次真实评测的逐题证据与聚合指标写入 JSON 日志。"""
    started_at = datetime.now(UTC)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / (
        f"computer_architecture_pdf_quality-{started_at.strftime('%Y%m%d-%H%M%S-%f')}.json"
    )
    outcome_by_id = {outcome.case_id: outcome for outcome in outcomes}
    payload = {
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "pdf_path": str(pdf_path),
        "fixture_path": str(FIXTURE_PATH),
        "case_count": len(cases),
        "top_k": 6,
        "cases": [
            _case_log_record(
                case,
                embeddings[case.id],
                results[case.id],
                outcome_by_id[case.id],
            )
            for case in cases
        ],
        "metrics": {
            "recall_at_6": metrics.recall_at_6,
            "mrr_at_6": metrics.mrr_at_6,
            "top1_page_hit": metrics.top1_page_hit,
            "answer_coverage": metrics.answer_coverage,
        },
    }
    log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return log_path


def test_case_log_record_preserves_embedding_and_top_k_details() -> None:
    """评测日志必须保留向量、候选排名与各阶段分数。"""
    case = KnowledgeCase(
        "arch-001", "第一章 计算机系统概论", "query", (1,), "answer", ("answer", "term")
    )
    evidence = type(
        "Evidence",
        (),
        {
            "chunk_id": "chunk-1",
            "document_id": "document-1",
            "index_version": 1,
            "locator": type("Locator", (), {"page_number": 1})(),
            "source_name": "source.pdf",
            "scores": type(
                "Scores",
                (),
                {
                    "dense_score": 0.9,
                    "sparse_score": 0.8,
                    "fusion_score": 0.7,
                    "rerank_score": None,
                    "HasField": lambda self, name: name != "rerank_score",
                },
            )(),
            "content_with_weight": "answer term",
        },
    )()
    result = type("Result", (), {"evidence": [evidence]})()
    outcome = CaseOutcome("arch-001", True, 1.0, True, True, (1,), ())

    record = _case_log_record(case, (0.1, 0.2), result, outcome)

    assert record["embedding"] == [0.1, 0.2]
    assert record["top_k"][0]["rank"] == 1
    assert record["top_k"][0]["chunk_id"] == "chunk-1"
    assert record["top_k"][0]["scores"]["fusion_score"] == 0.7
    assert record["top_k"][0]["content_with_weight"] == "answer term"


def test_case_log_record_truncates_embedding_to_first_20_dimensions() -> None:
    """日志仅保留前 20 维 Embedding，避免诊断文件过大。"""
    case = KnowledgeCase(
        "arch-001", "第一章 计算机系统概论", "query", (1,), "answer", ("answer", "term")
    )
    outcome = CaseOutcome("arch-001", True, 1.0, True, True, (1,), ())
    result = type("Result", (), {"evidence": []})()
    embedding = tuple(float(index) for index in range(24))

    record = _case_log_record(case, embedding, result, outcome)

    assert record["embedding"] == [float(index) for index in range(20)]
    assert len(record["embedding"]) == 20


def test_write_run_log_persists_json_with_completion_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """评测日志写入后应包含完成时间和指定输出目录。"""
    monkeypatch.setattr(__import__(__name__), "LOG_DIR", tmp_path)
    case = KnowledgeCase("arch-001", "第一章 计算机系统概论", "query", (1,), "answer", ("answer",))
    outcome = CaseOutcome("arch-001", True, 1.0, True, True, (1,), ())
    metrics = QualityMetrics(1.0, 1.0, 1.0, 1.0)
    result = type("Result", (), {"evidence": []})()

    log_path = _write_run_log(
        (case,),
        {"arch-001": (0.1, 0.2)},
        {"arch-001": result},
        (outcome,),
        metrics,
        pdf_path=Path("source.pdf"),
    )

    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert log_path.parent == tmp_path
    assert payload["finished_at"]


def test_original_and_rephrased_fixtures_only_change_query() -> None:
    """原始与改写题集只能改变查询文本，标准答案与标签必须一致。"""
    original = json.loads(_fixture_path_for_variant("original").read_text(encoding="utf-8"))
    rephrased = json.loads(_fixture_path_for_variant("rephrased").read_text(encoding="utf-8"))

    assert len(original) == len(rephrased) == 50
    for original_case, rephrased_case in zip(original, rephrased, strict=True):
        assert original_case["id"] == rephrased_case["id"]
        assert original_case["query"] != rephrased_case["query"]
        assert {**original_case, "query": None} == {**rephrased_case, "query": None}


def _compact_text(value: str) -> str:
    """移除空白后再做短语覆盖判断，避免排版差异造成误判。"""
    return "".join(value.split())


def _load_cases(path: Path) -> tuple[KnowledgeCase, ...]:
    """读取并严格校验 50 道计组 PDF 质量评测题。"""
    payloads = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payloads, list)
    assert len(payloads) == 50
    cases: list[KnowledgeCase] = []
    for item_number, payload in enumerate(payloads, start=1):
        assert isinstance(payload, dict), item_number
        assert set(payload) == {
            "id",
            "chapter",
            "query",
            "pages",
            "answer",
            "required_phrases",
        }, item_number
        case = KnowledgeCase(
            id=str(payload["id"]),
            chapter=str(payload["chapter"]),
            query=str(payload["query"]),
            pages=tuple(int(page) for page in payload["pages"]),
            answer=str(payload["answer"]),
            required_phrases=tuple(str(phrase) for phrase in payload["required_phrases"]),
        )
        assert case.query.strip() and case.answer.strip(), case.id
        assert case.pages == tuple(sorted(set(case.pages))) and case.pages, case.id
        assert case.chapter in EXPECTED_CHAPTER_PAGES, case.id
        assert set(case.pages) <= EXPECTED_CHAPTER_PAGES[case.chapter], case.id
        assert len(case.required_phrases) >= 2, case.id
        assert len(set(case.required_phrases)) == len(case.required_phrases), case.id
        compact_answer = _compact_text(case.answer)
        assert all(
            phrase.strip() and _compact_text(phrase) in compact_answer
            for phrase in case.required_phrases
        ), case.id
        cases.append(case)

    assert [case.id for case in cases] == [f"arch-{index:03d}" for index in range(1, 51)]
    assert Counter(case.chapter for case in cases) == Counter(EXPECTED_CHAPTER_COUNTS)
    return tuple(cases)


@pytest.fixture
def computer_architecture_pdf() -> Path:
    """定位可选的本地真实 PDF；缺失时跳过而非伪造评测。"""
    configured_path = os.getenv(LOCAL_PDF_ENV, "").strip()
    source = Path(configured_path).expanduser() if configured_path else DEFAULT_LOCAL_PDF
    if not source.is_file():
        pytest.skip(
            "local real-user PDF is unavailable; place it at "
            f"{DEFAULT_LOCAL_PDF} or set {LOCAL_PDF_ENV} to a container-visible path"
        )
    return source


def _evaluate_case(case: KnowledgeCase, result: Any, document_id: str) -> CaseOutcome:
    """从指定文档的 Top-6 evidence 计算单题召回、MRR 与覆盖率。"""
    evidence = tuple(item for item in result.evidence[:6] if str(item.document_id) == document_id)
    retrieved_pages = tuple(item.locator.page_number for item in evidence)
    relevant_pages = set(case.pages)
    first_relevant_rank = next(
        (
            rank
            for rank, page_number in enumerate(retrieved_pages, start=1)
            if page_number in relevant_pages
        ),
        None,
    )
    context = _compact_text("".join(item.content_with_weight for item in evidence))
    missing_phrases = tuple(
        phrase for phrase in case.required_phrases if _compact_text(phrase) not in context
    )
    return CaseOutcome(
        case_id=case.id,
        hit=first_relevant_rank is not None,
        reciprocal_rank=0.0 if first_relevant_rank is None else 1.0 / first_relevant_rank,
        top1_page_hit=bool(retrieved_pages and retrieved_pages[0] in relevant_pages),
        answer_covered=not missing_phrases,
        retrieved_pages=retrieved_pages,
        missing_phrases=missing_phrases,
    )


def _aggregate(outcomes: tuple[CaseOutcome, ...]) -> QualityMetrics:
    """汇总 50 道题的 Recall@6、MRR、首页命中和答案覆盖指标。"""
    count = len(outcomes)
    assert count == 50
    return QualityMetrics(
        recall_at_6=sum(outcome.hit for outcome in outcomes) / count,
        mrr_at_6=sum(outcome.reciprocal_rank for outcome in outcomes) / count,
        top1_page_hit=sum(outcome.top1_page_hit for outcome in outcomes) / count,
        answer_coverage=sum(outcome.answer_covered for outcome in outcomes) / count,
    )


@pytest.mark.eval
@pytest.mark.e2e
@pytest.mark.asyncio
async def test_real_computer_architecture_pdf_quality(
    computer_architecture_pdf: Path,
    rag_stub: object,
    embedding_runtime: EmbeddingRuntime,
) -> None:
    """在 Docker 真实链路上执行 50 题 PDF 检索质量门禁并记录诊断。"""
    cases = _load_cases(FIXTURE_PATH)
    dataset_id = await create_dataset(
        rag_stub,
        embedding_runtime,
        "real-computer-architecture-pdf-50",
    )
    try:
        document_id, job_id = await submit_document(
            rag_stub,
            dataset_id,
            computer_architecture_pdf,
        )
        await wait_for_job(rag_stub, job_id, deadline_seconds=600)

        outcomes: list[CaseOutcome] = []
        results: dict[str, Any] = {}
        embeddings: dict[str, tuple[float, ...]] = {}
        endpoint = os.getenv("EMBEDDING_MODEL_URL", "").strip()
        api_key = os.getenv("EMBEDDING_MODEL_API_KEY", "").strip()
        if not endpoint or not api_key:
            pytest.fail("real eval requires embedding endpoint and API key for query capture")
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=float(os.getenv("RAG_EMBEDDING_TIMEOUT_SECONDS", "30")),
        ) as client:
            gateway = OpenAICompatibleModelGateway(
                client,
                endpoint,
                embedding_runtime.model,
                embedding_runtime.dimension,
                int(os.getenv("RAG_EMBEDDING_BATCH_SIZE", "32")),
                int(os.getenv("RAG_EMBEDDING_MAX_RETRIES", "3")),
            )
            # 每题同时捕获查询向量和 gRPC 召回结果，便于失败后复盘。
            for case in cases:
                embeddings[case.id] = (await gateway.embed([case.query]))[0]
                result = await retrieve(rag_stub, dataset_id, case.query)
                results[case.id] = result
                outcomes.append(_evaluate_case(case, result, document_id))

        frozen_outcomes = tuple(outcomes)
        metrics = _aggregate(frozen_outcomes)
        diagnostics = [
            (
                f"{outcome.case_id}: pages={outcome.retrieved_pages!r} "
                f"hit={outcome.hit} rr={outcome.reciprocal_rank:.3f} "
                f"top1={outcome.top1_page_hit} missing={outcome.missing_phrases!r}"
            )
            for outcome in frozen_outcomes
            if not (
                outcome.hit
                and outcome.top1_page_hit
                and outcome.answer_covered
                and outcome.reciprocal_rank == 1.0
            )
        ]
        summary = (
            f"recall@6={metrics.recall_at_6:.3f} mrr@6={metrics.mrr_at_6:.3f} "
            f"top1_page_hit={metrics.top1_page_hit:.3f} "
            f"answer_coverage={metrics.answer_coverage:.3f}"
        )
        log_path = _write_run_log(
            cases,
            embeddings,
            results,
            frozen_outcomes,
            metrics,
            pdf_path=computer_architecture_pdf,
        )
        print(f"log={log_path}")
        print(summary)
        failures = []
        if metrics.recall_at_6 < RECALL_AT_6_THRESHOLD:
            failures.append(f"recall@6 < {RECALL_AT_6_THRESHOLD:.2f}")
        if metrics.mrr_at_6 < MRR_AT_6_THRESHOLD:
            failures.append(f"mrr@6 < {MRR_AT_6_THRESHOLD:.2f}")
        if metrics.top1_page_hit < TOP1_PAGE_HIT_THRESHOLD:
            failures.append(f"top1_page_hit < {TOP1_PAGE_HIT_THRESHOLD:.2f}")
        if metrics.answer_coverage < ANSWER_COVERAGE_THRESHOLD:
            failures.append(f"answer_coverage < {ANSWER_COVERAGE_THRESHOLD:.2f}")
        assert not failures, "\n".join([summary, *failures, *diagnostics])
    finally:
        deletion_job_id = await delete_dataset(rag_stub, dataset_id)
        await wait_for_dataset_purged(rag_stub, deletion_job_id)
