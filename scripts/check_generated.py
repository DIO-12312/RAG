"""Fail when checked-in Python protobuf artifacts differ from the proto source."""

# 在临时目录重新生成产物，防止调用方仅修改 `.proto` 却遗漏提交 Python 生成代码。

from __future__ import annotations

import tempfile
from pathlib import Path

from generate_proto import GENERATED_DIR, GENERATED_FILES, generate


def normalized_generated_text(path: Path) -> str:
    """Read generated text with platform line endings normalized to LF."""

    # 消除平台换行差异，避免同一生成物在 Windows/Linux 上误报不同。

    return path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")


def generated_files_match(checked_in: Path, regenerated: Path) -> bool:
    """Return whether generated artifacts have identical textual content."""

    # 用统一换行后的文本比较，避免跨平台开发环境产生无意义的契约漂移。
    return normalized_generated_text(checked_in) == normalized_generated_text(regenerated)


def main() -> int:
    """Regenerate in a temporary directory and compare every expected artifact.

    临时目录保证检查无副作用：失败时不修改已提交的 generated 文件。
    """

    mismatches: list[str] = []
    # 在临时目录再生，检查过程绝不覆写调用方工作区中的已提交文件。
    with tempfile.TemporaryDirectory(prefix="rag-proto-check-") as directory:
        temporary_output = Path(directory)
        generate(temporary_output)

        for filename in GENERATED_FILES:
            checked_in = GENERATED_DIR / filename
            regenerated = temporary_output / filename
            if not checked_in.is_file():
                mismatches.append(f"missing generated file: {checked_in}")
            elif not generated_files_match(checked_in, regenerated):
                mismatches.append(f"out-of-date generated file: {checked_in}")

    if mismatches:
        # 汇总所有缺失或过期项，便于一次性修复，而非遇到第一项即退出。
        print("\n".join(mismatches))
        print("Run: uv run python scripts/generate_proto.py")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
