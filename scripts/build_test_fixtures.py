"""Build deterministic binary fixtures that cannot be maintained as text patches."""

# mypy: disable-error-code=import-untyped

from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF = ROOT / "tests" / "fixtures" / "documents" / "manual.pdf"


def build_manual_pdf() -> bytes:
    buffer = BytesIO()
    document = canvas.Canvas(
        buffer,
        pagesize=letter,
        pageCompression=1,
        invariant=1,
    )
    document.setTitle("Quartz Beacon Maintenance Manual")
    document.setAuthor("RAG MVP deterministic fixture builder")
    document.setFont("Helvetica-Bold", 14)
    document.drawString(72, 740, "Quartz Beacon Maintenance")
    document.setFont("Helvetica", 11)
    document.drawString(72, 710, "The Quartz Beacon calibration interval is thirty-seven days.")
    document.drawString(
        72, 690, "Technicians record each calibration in the beacon maintenance ledger."
    )
    document.showPage()
    document.save()
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description="build deterministic test fixtures")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_PDF)
    arguments = parser.parse_args()
    expected = build_manual_pdf()
    if arguments.check:
        if not arguments.output.is_file() or arguments.output.read_bytes() != expected:
            print("deterministic PDF fixture is missing or stale")
            return 1
        return 0
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    if not arguments.output.is_file() or arguments.output.read_bytes() != expected:
        arguments.output.write_bytes(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
