"""Fail when checked-in Python protobuf artifacts differ from the proto source."""

from __future__ import annotations

import tempfile
from pathlib import Path

from generate_proto import GENERATED_DIR, GENERATED_FILES, generate


def normalized_generated_text(path: Path) -> str:
    """Read generated text with platform line endings normalized to LF."""

    return path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")


def generated_files_match(checked_in: Path, regenerated: Path) -> bool:
    """Return whether generated artifacts have identical textual content."""

    return normalized_generated_text(checked_in) == normalized_generated_text(regenerated)


def main() -> int:
    """Regenerate in a temporary directory and compare every expected artifact."""

    mismatches: list[str] = []
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
        print("\n".join(mismatches))
        print("Run: uv run python scripts/generate_proto.py")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
