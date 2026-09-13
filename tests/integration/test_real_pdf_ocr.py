from __future__ import annotations

import shutil
from io import BytesIO

import pytest
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from rag_mvp.adapters.parsers.pdf import PdfParser
from rag_mvp.ports.parser import PdfParserMode


def _scanned_pdf() -> bytes:
    image = Image.new("RGB", (1400, 260), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 54)
    draw.text((60, 80), "DDS OCR RETCODE TIMEOUT", fill="black", font=font)
    image_bytes = BytesIO()
    image.save(image_bytes, format="PNG")
    image_bytes.seek(0)

    pdf_bytes = BytesIO()
    canvas = Canvas(pdf_bytes, pagesize=letter)
    canvas.drawImage(ImageReader(image_bytes), 40, 560, width=532, height=100)
    canvas.showPage()
    canvas.save()
    return pdf_bytes.getvalue()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runtime_poppler_tesseract_ocr_extracts_a_scanned_pdf() -> None:
    assert shutil.which("pdftoppm"), "runtime image must contain Poppler"
    assert shutil.which("tesseract"), "runtime image must contain Tesseract"

    segments = await PdfParser(
        mode=PdfParserMode.DEEPDOC,
        native_text_min_chars_per_page=1,
        ocr_language="chi_sim+eng",
    ).parse("scanned.pdf", _scanned_pdf())

    text = "\n".join(segment.text for segment in segments).upper()
    assert "DDS OCR" in text
    assert "TIMEOUT" in text
    assert all(segment.metadata["extraction_method"] == "ocr" for segment in segments)
