from __future__ import annotations

from rag_mvp.domain.models import Evidence, Locator, ScoreBreakdown
from rag_mvp.retrieval.context_builder import build_context_plan, estimate_tokens


def _evidence(chunk_id: str, content: str) -> Evidence:
    return Evidence(
        chunk_id=chunk_id,
        document_id="document-1",
        content_with_weight=content,
        source_name="guide.txt",
        locator=Locator(start_line=1, end_line=1),
        scores=ScoreBreakdown(dense_score=0.8),
        index_version=1,
    )


def test_context_builder_keeps_whole_evidence_within_budget() -> None:
    evidence = (_evidence("a", "12345678"), _evidence("b", "abcdefgh"))

    plan = build_context_plan(evidence, max_context_tokens=2)

    assert [item.chunk_id for item in plan.evidence] == ["a"]
    assert plan.estimated_tokens == 2
    assert plan.omitted_chunk_ids == ("b",)


def test_token_estimate_is_deterministic_and_nonzero() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1
    assert estimate_tokens("12345") == 2
