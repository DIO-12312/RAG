from __future__ import annotations

# 确保提交的 protobuf 生成代码可由当前契约确定性再生成。
import subprocess
import sys
from pathlib import Path


def test_generated_python_is_in_sync_with_proto() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    repository_root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, "scripts/check_generated.py"],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
