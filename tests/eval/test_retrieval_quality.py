from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_mvp.retrieval.evaluation import EvaluationCase, evaluate_rankings


@pytest.mark.eval
def test_fixed_thirty_question_quality_baseline() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "retrieval_quality.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    cases = tuple(
        EvaluationCase(
            relevant_chunk_ids=tuple(item["relevant_chunk_ids"]),
            retrieved_chunk_ids=tuple(item["retrieved_chunk_ids"]),
            locator_matches=item["locator_matches"],
        )
        for item in fixture
    )

    metrics = evaluate_rankings(cases, k=6)

    assert len(cases) >= 30
    assert metrics.recall_at_k >= 0.85
    assert metrics.mrr_at_k >= 0.70
    assert metrics.locator_accuracy == 1.0
