from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.mark.resilience
def test_every_spec_invariant_has_mock_evidence_and_explicit_real_validation_status() -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "reliability_matrix.json"
    matrix = json.loads(fixture.read_text(encoding="utf-8"))

    assert {item["id"] for item in matrix} == {f"T{index}" for index in range(1, 26)}
    assert all(item["mock_tests"] for item in matrix)
    assert all(isinstance(item["real_validation_required"], bool) for item in matrix)
    assert sum(item["real_validation_required"] for item in matrix) == 22
