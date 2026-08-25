from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_generated_python_is_in_sync_with_proto() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, "scripts/check_generated.py"],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
