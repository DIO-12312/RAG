from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas

from rag_mvp.adapters.parsers.pdf import PdfLayoutLine, PdfParser
from rag_mvp.domain.errors import DomainError
from rag_mvp.ports.parser import PdfParserMode


class _FakeOcr:
    def __init__(self, *, available: bool = True) -> None:
        self._available = available
        self.pages: list[int] = []

    def available(self) -> bool:
        return self._available

    def extract(
        self,
        content: bytes,
        *,
        page_number: int,
        page_width: float,
        page_height: float,
        dpi: int,
        language: str,
        timeout_seconds: float,
    ) -> tuple[PdfLayoutLine, ...]:
        del content, page_width, page_height, dpi, language, timeout_seconds
        self.pages.append(page_number)
        return (
            PdfLayoutLine(
                text="扫描页故障代码 DDS_RETCODE_TIMEOUT",
                x0=72,
                top=96,
                x1=420,
                bottom=112,
                font_size=12,
                confidence=93.5,
                extraction_method="ocr",
            ),
        )


def _layout_pdf() -> bytes:
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=letter)
    for page_number in range(1, 4):
        canvas.setFont("Helvetica", 9)
        canvas.drawString(72, 775, "ZRDDS Internal Manual")
        canvas.drawString(280, 20, f"Page {page_number}")
        canvas.setFont("Helvetica-Bold", 18)
        canvas.drawString(72, 720, f"Section {page_number}")
        canvas.setFont("Helvetica", 10)
        canvas.drawString(72, 690, "Detailed configuration evidence for the current section.")
        canvas.drawString(72, 670, "The paragraph remains attached to its heading and page.")
        canvas.setFont("Helvetica-Bold", 12)
        canvas.drawString(72, 645, "Chapter entry ........................ 12")
        canvas.drawString(72, 625, "Task 3")
        canvas.setFont("Helvetica", 10)
        if page_number == 2:
            for index, values in enumerate(
                (("Field", "Type", "Meaning"), ("reliability", "enum", "QoS policy"))
            ):
                y = 620 - index * 20
                canvas.drawString(72, y, values[0])
                canvas.drawString(260, y, values[1])
                canvas.drawString(430, y, values[2])
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def _blank_pdf() -> bytes:
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=letter)
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_deepdoc_pdf_preserves_heading_bbox_table_and_removes_repeated_margins() -> None:
    segments = await PdfParser(
        mode=PdfParserMode.DEEPDOC,
        native_text_min_chars_per_page=1,
    ).parse("manual.pdf", _layout_pdf())

    joined = "\n".join(segment.text for segment in segments)
    assert "ZRDDS Internal Manual" not in joined
    assert "Page 1" not in joined
    assert "Section 1" in segments[0].metadata["heading_path"]
    assert all("Chapter entry" not in segment.metadata["heading_path"] for segment in segments)
    assert all("Task 3" not in segment.metadata["heading_path"] for segment in segments)
    assert all(segment.locator.page_number in {1, 2, 3} for segment in segments)
    assert all(segment.metadata["bbox"].startswith("[") for segment in segments)
    assert all(
        segment.locator.metadata["coordinate_space"] == "pdf_points_top_left"
        for segment in segments
    )
    tables = [segment for segment in segments if segment.metadata["layout_type"] == "table"]
    assert tables
    assert "| Field | Type | Meaning |" in tables[0].text
    assert {segment.metadata.get("printed_page_number") for segment in segments} == {
        "1",
        "2",
        "3",
    }


@pytest.mark.asyncio
async def test_deepdoc_pdf_uses_ocr_for_a_scanned_page_and_keeps_confidence() -> None:
    ocr = _FakeOcr()
    segments = await PdfParser(
        mode=PdfParserMode.DEEPDOC,
        native_text_min_chars_per_page=1,
        ocr_engine=ocr,
    ).parse("scan.pdf", _blank_pdf())

    assert ocr.pages == [1]
    assert len(segments) == 1
    assert "DDS_RETCODE_TIMEOUT" in segments[0].text
    assert segments[0].metadata["extraction_method"] == "ocr"
    assert segments[0].metadata["ocr_confidence"] == "93.50"


@pytest.mark.asyncio
async def test_forced_deepdoc_rejects_scanned_pdf_when_ocr_is_unavailable() -> None:
    parser = PdfParser(
        mode=PdfParserMode.DEEPDOC,
        native_text_min_chars_per_page=1,
        ocr_engine=_FakeOcr(available=False),
    )

    with pytest.raises(DomainError) as error:
        await parser.parse("scan.pdf", _blank_pdf())

    assert error.value.failure.code == "PDF_OCR_UNAVAILABLE"


@pytest.mark.asyncio
async def test_auto_mode_degrades_to_native_content_without_ocr_tools() -> None:
    segments = await PdfParser(
        mode=PdfParserMode.AUTO,
        native_text_min_chars_per_page=10_000,
        ocr_engine=_FakeOcr(available=False),
    ).parse("manual.pdf", _layout_pdf())

    assert segments
    assert all(segment.metadata["extraction_method"] == "native" for segment in segments)


@pytest.mark.asyncio
async def test_pdf_page_limit_fails_before_ocr() -> None:
    with pytest.raises(DomainError) as error:
        await PdfParser(max_pages=2).parse("manual.pdf", _layout_pdf())

    assert error.value.failure.code == "PDF_PAGE_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_pdfplumber_keeps_scaled_contents_and_repeated_pages_deterministic() -> None:
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=letter)
    canvas.setFont("Helvetica-Bold", 18)
    canvas.drawString(72, 720, "Contents")
    # Text objects end independently, and use a scaled graphics matrix.
    canvas.saveState()
    canvas.scale(0.6, 0.6)
    for row, title in enumerate(("Warm-up", "Basic Concepts", "Scheduling Criteria")):
        canvas.setFont("Helvetica", 10)
        canvas.drawString(120, 1100 - row * 30, str(row + 1))
        canvas.drawString(150, 1100 - row * 30, title)
    canvas.restoreState()
    canvas.showPage()
    canvas.setFont("Helvetica-Bold", 18)
    canvas.drawString(72, 720, "Scheduling")
    for row in range(3):
        canvas.setFont("Helvetica-Bold", 10)
        canvas.drawString(72, 650 - row * 24, "Q")
        canvas.setFont("Helvetica", 7)
        canvas.drawString(80, 648 - row * 24, str(2 - row))
        canvas.setFont("Helvetica", 10)
        for col in range(row, 6):
            canvas.drawString(120 + col * 24, 650 - row * 24, "A" if row == 2 else "B")
    canvas.setFont("Helvetica", 10)
    canvas.drawString(72, 550, "T")
    canvas.setFont("Helvetica", 7)
    canvas.drawString(78, 548, "turnaround")
    canvas.showPage()
    canvas.save()
    reader = PdfReader(buffer)
    writer = PdfWriter()
    for page in (reader.pages[0], reader.pages[1], reader.pages[0]):
        writer.add_page(page)
    repeated = BytesIO()
    writer.write(repeated)
    segments = await PdfParser(native_text_min_chars_per_page=1).parse(
        "grid.pdf", repeated.getvalue()
    )
    contents = "\n".join(s.text for s in segments if s.locator.page_number == 1)
    assert "1 Warm-up" in " ".join(contents.split())
    assert "2 Basic Concepts" in " ".join(contents.split())
    grid = "\n".join(s.text for s in segments if s.locator.page_number == 2)
    assert grid.count("Q") == 3
    assert all(label in grid for label in ("0", "1", "2"))
    assert sum(line.count("B") for line in grid.splitlines()) == 11
    assert "T" in grid and "turnaround" in grid
    assert contents == "\n".join(s.text for s in segments if s.locator.page_number == 3)


@pytest.mark.asyncio
async def test_pdfplumber_uses_bold_labels_as_heading_boundaries() -> None:
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=letter)
    canvas.setFont("Helvetica-Bold", 18)
    canvas.drawString(72, 720, "Scheduling Algorithms")
    for row, text in enumerate(("CPU", "A", "RR", "Dispatcher", "systems")):
        canvas.setFont("Helvetica-Bold" if row < 4 else "Helvetica", 10)
        canvas.drawString(72, 670 - row * 40, text)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(72, 20, "Unusual footer 1 / 1")
    canvas.showPage()
    canvas.save()
    segments = await PdfParser(native_text_min_chars_per_page=1).parse(
        "labels.pdf", buffer.getvalue()
    )
    assert segments
    assert any("Dispatcher" in segment.metadata["heading_path"] for segment in segments)
    assert "systems" in "\n".join(segment.text for segment in segments)


@pytest.mark.asyncio
async def test_pdfplumber_recognizes_repeated_multicolumn_rows_as_tables() -> None:
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=letter)
    for page in range(3):
        canvas.setFont("Helvetica", 10)
        for row in range(2):
            canvas.drawString(72, 650 - row * 20, "- process" if page == 2 else "process")
            canvas.drawString(260 + (row * 60 if page == 1 else 0), 650 - row * 20, "value")
        canvas.showPage()
    canvas.save()
    segments = await PdfParser(native_text_min_chars_per_page=1).parse(
        "columns.pdf", buffer.getvalue()
    )
    assert [s.locator.page_number for s in segments if s.metadata["layout_type"] == "table"] == [
        1,
        2,
        3,
    ]
    assert all("process" in s.text and "value" in s.text for s in segments)
