from __future__ import annotations

# 验证多格式解析器将不同文件统一为带定位信息的标准分段。
from io import BytesIO

import pytest
from reportlab.pdfgen.canvas import Canvas

from rag_mvp.adapters.parsers.code import CodeParser
from rag_mvp.adapters.parsers.markdown import MarkdownParser
from rag_mvp.adapters.parsers.pdf import PdfParser
from rag_mvp.adapters.parsers.router import SourceParserRouter
from rag_mvp.domain.errors import DomainError


@pytest.mark.asyncio
async def test_markdown_parser_preserves_heading_sections_and_lines() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    segments = await MarkdownParser().parse("guide.md", b"# Intro\nAlpha\n\n## Details\nBeta")

    assert [segment.text for segment in segments] == ["# Intro\nAlpha", "## Details\nBeta"]
    assert [segment.metadata["section"] for segment in segments] == ["Intro", "Details"]
    assert [(item.locator.start_line, item.locator.end_line) for item in segments] == [
        (1, 2),
        (4, 5),
    ]


@pytest.mark.asyncio
async def test_code_parser_preserves_language_symbols_and_lines() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    source = (
        b"class Greeter:\n    def hello(self):\n        return 'hi'\n\ndef helper():\n    return 1"
    )

    segments = await CodeParser().parse("greeter.py", source)

    assert [segment.locator.symbol for segment in segments] == ["Greeter", "helper"]
    assert all(segment.locator.language == "python" for segment in segments)
    assert [(item.locator.start_line, item.locator.end_line) for item in segments] == [
        (1, 3),
        (5, 6),
    ]
    assert all(segment.metadata["source_type"] == "code" for segment in segments)


def _text_pdf() -> bytes:
    """构造本测试所需的输入、替身或运行环境。"""
    buffer = BytesIO()
    canvas = Canvas(buffer)
    canvas.drawString(72, 720, "First page evidence")
    canvas.showPage()
    canvas.drawString(72, 720, "Second page provenance")
    canvas.save()
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_pdf_parser_returns_one_traceable_segment_per_text_page() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    segments = await PdfParser().parse("guide.pdf", _text_pdf())

    assert [segment.locator.page_number for segment in segments] == [1, 2]
    assert "First page evidence" in segments[0].text
    assert "Second page provenance" in segments[1].text
    assert all(segment.metadata == {"source_type": "pdf"} for segment in segments)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_name", "source_type"),
    [
        ("guide.TXT", "text"),
        ("guide.md", "markdown"),
        ("main.go", "code"),
        ("guide.pdf", "pdf"),
    ],
)
async def test_router_selects_supported_parser(source_name: str, source_type: str) -> None:
    """验证本测试场景的预期行为与边界条件。"""
    content = _text_pdf() if source_name.casefold().endswith(".pdf") else b"plain content"

    segments = await SourceParserRouter().parse(source_name, content)

    assert segments
    assert segments[0].metadata["source_type"] == source_type


@pytest.mark.asyncio
async def test_router_rejects_unsupported_source_type() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    with pytest.raises(DomainError) as error:
        await SourceParserRouter().parse("archive.docx", b"unsupported")

    assert error.value.failure.code == "UNSUPPORTED_SOURCE_TYPE"


@pytest.mark.asyncio
async def test_pdf_parser_rejects_corrupt_bytes() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    with pytest.raises(DomainError) as error:
        await PdfParser().parse("bad.pdf", b"not a pdf")

    assert error.value.failure.code == "INVALID_PDF"
