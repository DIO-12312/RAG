from __future__ import annotations

# 将 SPEC 中关键可靠性承诺映射为可执行的测试覆盖检查。
import json
from pathlib import Path

import pytest


@pytest.mark.resilience
def test_every_spec_invariant_has_mock_evidence_and_explicit_real_validation_status() -> None:
    """规格不变量必须有 Mock 证据，并声明真实环境的验证状态。"""
    fixture = Path(__file__).parents[1] / "fixtures" / "reliability_matrix.json"
    matrix = json.loads(fixture.read_text(encoding="utf-8"))

    assert {item["id"] for item in matrix} == {f"T{index}" for index in range(1, 26)}
    assert all(item["mock_tests"] for item in matrix)
    assert all(isinstance(item["real_validation_required"], bool) for item in matrix)
    assert sum(item["real_validation_required"] for item in matrix) == 22
    assert all(isinstance(item["real_tests"], list) for item in matrix)
    assert all(item["real_tests"] if item["real_validation_required"] else True for item in matrix)
    for item in matrix:
        for evidence in item["real_tests"]:
            relative_path, separator, function_name = evidence.partition("::")
            source = fixture.parents[1] / relative_path
            assert source.is_file(), evidence
            if separator:
                assert f"def {function_name}(" in source.read_text(encoding="utf-8"), evidence
