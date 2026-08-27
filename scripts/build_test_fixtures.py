"""Build deterministic binary fixtures that cannot be maintained as text patches.

生成可复现的二进制 PDF 测试夹具，避免将不可读的二进制差异直接维护在补丁中。
"""

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
    """构造字节级稳定的两页 PDF，供 PDF 解析与摄取测试复用。"""

    # invariant 固定 PDF 中原本会随时间或环境变化的元数据，确保 --check 可比较字节。
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
    """生成夹具；--check 仅验证已提交文件，没有写入副作用。"""

    parser = argparse.ArgumentParser(description="build deterministic test fixtures")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_PDF)
    arguments = parser.parse_args()
    expected = build_manual_pdf()
    if arguments.check:
        # 检查模式只比较字节，供 CI 发现夹具遗漏或被非确定性工具改写的情况。
        if not arguments.output.is_file() or arguments.output.read_bytes() != expected:
            print("deterministic PDF fixture is missing or stale")
            return 1
        return 0
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    # 内容相同时不改写文件，避免无意义的时间戳或工作区变更。
    if not arguments.output.is_file() or arguments.output.read_bytes() != expected:
        arguments.output.write_bytes(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
