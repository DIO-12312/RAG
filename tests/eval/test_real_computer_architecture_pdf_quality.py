"""Real 50-question quality gate for the local computer-architecture PDF."""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.conftest import (
    EmbeddingRuntime,
    create_dataset,
    retrieve,
    submit_document,
    wait_for_job,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "computer_architecture_knowledge.json"
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


def _compact_text(value: str) -> str:
    return "".join(value.split())


def _load_cases(path: Path) -> tuple[KnowledgeCase, ...]:
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
    configured_path = os.getenv(LOCAL_PDF_ENV, "").strip()
    source = Path(configured_path).expanduser() if configured_path else DEFAULT_LOCAL_PDF
    if not source.is_file():
        pytest.skip(
            "local real-user PDF is unavailable; place it at "
            f"{DEFAULT_LOCAL_PDF} or set {LOCAL_PDF_ENV} to a container-visible path"
        )
    return source


def _evaluate_case(case: KnowledgeCase, result: Any, document_id: str) -> CaseOutcome:
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
    cases = _load_cases(FIXTURE_PATH)
    dataset_id = await create_dataset(
        rag_stub,
        embedding_runtime,
        "real-computer-architecture-pdf-50",
    )
    document_id, job_id = await submit_document(
        rag_stub,
        dataset_id,
        computer_architecture_pdf,
    )
    await wait_for_job(rag_stub, job_id, deadline_seconds=600)

    outcomes: list[CaseOutcome] = []
    for case in cases:
        result = await retrieve(rag_stub, dataset_id, case.query)
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
