"""Cross-platform comparison rules for checked-in protobuf artifacts."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from check_generated import generated_files_match  # noqa: E402


def test_generated_comparison_ignores_only_line_endings(tmp_path: Path) -> None:
    """验证本测试场景的预期行为与边界条件。"""
    checked = tmp_path / "checked.py"
    regenerated = tmp_path / "regenerated.py"
    checked.write_bytes(b"alpha\r\nbeta\r\n")
    regenerated.write_bytes(b"alpha\nbeta\n")

    assert generated_files_match(checked, regenerated)


def test_generated_comparison_rejects_content_changes(tmp_path: Path) -> None:
    """验证本测试场景的预期行为与边界条件。"""
    checked = tmp_path / "checked.py"
    regenerated = tmp_path / "regenerated.py"
    checked.write_text("alpha\n", encoding="utf-8")
    regenerated.write_text("omega\n", encoding="utf-8")

    assert not generated_files_match(checked, regenerated)
