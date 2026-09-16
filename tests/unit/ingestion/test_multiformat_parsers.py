from __future__ import annotations

# 验证多格式解析器将不同文件统一为带定位信息的标准分段。
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from reportlab.pdfgen.canvas import Canvas

from rag_mvp.adapters.parsers.code import CodeParser
from rag_mvp.adapters.parsers.markdown import MarkdownParser
from rag_mvp.adapters.parsers.pdf import PdfParser
from rag_mvp.adapters.parsers.pptx import PptxParser
from rag_mvp.adapters.parsers.router import SourceParserRouter
from rag_mvp.adapters.parsers.text import TextParser
from rag_mvp.domain.errors import DomainError
from rag_mvp.ports.parser import PdfParserMode


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


@pytest.mark.asyncio
async def test_non_pdf_formats_do_not_fabricate_printed_page_numbers() -> None:
    parsed = (
        *(await TextParser().parse("guide.txt", b"plain text")),
        *(await MarkdownParser().parse("guide.md", b"# Heading\nbody")),
        *(await CodeParser().parse("main.py", b"def run():\n    return 1")),
    )

    assert parsed
    assert all(segment.locator.page_number is None for segment in parsed)
    assert all("printed_page_number" not in segment.locator.metadata for segment in parsed)


def _text_pdf() -> bytes:
    """构造本测试所需的输入、替身或运行环境。"""
    buffer = BytesIO()
    canvas = Canvas(buffer)
    canvas.drawString(72, 720, "First page evidence")
    canvas.showPage()
    canvas.drawString(72, 720, "Second page provenance")
    canvas.save()
    return buffer.getvalue()


def _text_pptx() -> bytes:
    """构造含两张有序文字幻灯片的最小 OOXML 演示文稿。"""
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<p:sldIdLst><p:sldId id="256" r:id="rId2"/><p:sldId id="257" r:id="rId1"/>'
            "</p:sldIdLst></p:presentation>",
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/slide" Target="slides/slide1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/slide" Target="slides/slide2.xml"/>'
            "</Relationships>",
        )
        for number, text in ((1, "Second declared slide"), (2, "First declared slide")):
            archive.writestr(
                f"ppt/slides/slide{number}.xml",
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                f"<a:t>{text}</a:t></p:sld>",
            )
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_pdf_parser_returns_one_traceable_segment_per_text_page() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    segments = await PdfParser(mode=PdfParserMode.PLAIN).parse("guide.pdf", _text_pdf())

    assert [segment.locator.page_number for segment in segments] == [1, 2]
    assert "First page evidence" in segments[0].text
    assert "Second page provenance" in segments[1].text
    assert all(segment.metadata["source_type"] == "pdf" for segment in segments)
    assert all(segment.metadata["parser_mode"] == "plain" for segment in segments)


@pytest.mark.asyncio
async def test_pptx_parser_preserves_slide_order_and_pdf_like_page_provenance() -> None:
    segments = await PptxParser().parse("guide.pptx", _text_pptx())

    assert [segment.text for segment in segments] == [
        "First declared slide",
        "Second declared slide",
    ]
    assert [segment.locator.page_number for segment in segments] == [1, 2]
    assert all(
        segment.metadata == {"source_type": "pptx", "parser_mode": "slides"} for segment in segments
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_name", "source_type"),
    [
        ("guide.TXT", "text"),
        ("guide.md", "markdown"),
        ("main.go", "code"),
        ("guide.pdf", "pdf"),
        ("guide.pptx", "pptx"),
    ],
)
async def test_router_selects_supported_parser(source_name: str, source_type: str) -> None:
    """验证本测试场景的预期行为与边界条件。"""
    content = (
        _text_pdf()
        if source_name.casefold().endswith(".pdf")
        else _text_pptx()
        if source_name.casefold().endswith(".pptx")
        else b"plain content"
    )

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


def test_pptx_archive_limits_are_aligned_with_the_upload_limit(monkeypatch) -> None:
    """单条目上限不得比单文件上限更严，否则合法的大讲稿会出现"上传成功但解析失败"。"""

    from rag_mvp.adapters import parsers as parsers_package

    assert parsers_package.pptx._MAX_ARCHIVE_ENTRY_BYTES == 64 * 1024 * 1024
    assert parsers_package.pptx._MAX_ARCHIVE_UNCOMPRESSED_BYTES == 256 * 1024 * 1024
    assert parsers_package.pptx._MAX_ARCHIVE_FILES == 4096


@pytest.mark.asyncio
async def test_pptx_parser_rejects_entries_and_totals_beyond_limits(monkeypatch) -> None:
    """压缩炸弹防护仍然生效：超单条目或超总展开字节一律以 INVALID_PPTX 拒绝。"""

    from rag_mvp.adapters import parsers as parsers_package

    monkeypatch.setattr(parsers_package.pptx, "_MAX_ARCHIVE_ENTRY_BYTES", 1024)
    with pytest.raises(DomainError) as entry_error:
        await PptxParser().parse("deck.pptx", _pptx_with_media(b"x" * 4096))
    assert entry_error.value.failure.code == "INVALID_PPTX"

    monkeypatch.setattr(parsers_package.pptx, "_MAX_ARCHIVE_ENTRY_BYTES", 64 * 1024 * 1024)
    monkeypatch.setattr(parsers_package.pptx, "_MAX_ARCHIVE_UNCOMPRESSED_BYTES", 2048)
    with pytest.raises(DomainError) as total_error:
        await PptxParser().parse("deck.pptx", _pptx_with_media(b"y" * 4096, stored=True))
    assert total_error.value.failure.code == "INVALID_PPTX"


def _pptx_with_media(payload: bytes, stored: bool = False) -> bytes:
    """在文本讲稿基础上追加一个媒体条目，用于验证归档上限。"""

    from io import BytesIO
    from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

    base = _text_pptx()
    buffer = BytesIO()
    compression = ZIP_STORED if stored else ZIP_DEFLATED
    with ZipFile(BytesIO(base)) as source, ZipFile(buffer, "w", compression) as target:
        for info in source.infolist():
            target.writestr(info, source.read(info))
        target.writestr("ppt/media/image1.png", payload, compress_type=ZIP_STORED)
    return buffer.getvalue()


class _RecordingImageOcr:
    """记录调用参数的图片 OCR 替身，避免测试依赖真实 Tesseract。"""

    def __init__(self, texts: dict[bytes, str] | None = None) -> None:
        self.calls: list[tuple[bytes, str, str]] = []
        self._texts = texts or {}

    def available(self) -> bool:
        return True

    async def extract(
        self,
        content: bytes,
        *,
        suffix: str,
        language: str,
        timeout_seconds: float,
    ) -> str:
        del timeout_seconds
        self.calls.append((content, suffix, language))
        return self._texts.get(content, f"识别文本 {len(content)}")


def _pptx_with_slide_images(*entries: tuple[str, bytes, str]) -> bytes:
    """构造带幻灯片图片关系的演示文稿：条目为 (媒体路径, 字节, 关系目标)。"""

    from io import BytesIO
    from zipfile import ZIP_STORED, ZipFile

    base = _text_pptx()
    buffer = BytesIO()
    with ZipFile(BytesIO(base)) as source, ZipFile(buffer, "w", ZIP_STORED) as target:
        for info in source.infolist():
            target.writestr(info, source.read(info))
        for media, payload, _target in entries:
            target.writestr(media, payload)
        for number in (1, 2):
            relations = "".join(
                f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/'
                f'officeDocument/2006/relationships/image" Target="{target}"/>'
                for index, (_media, _payload, target) in enumerate(entries, start=1)
            )
            target.writestr(
                f"ppt/slides/_rels/slide{number}.xml.rels",
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                f'relationships">{relations}</Relationships>',
            )
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_pptx_parser_merges_slide_image_text_with_slide_provenance() -> None:
    """截图文字必须并入所在幻灯片，并保留该页的页码来源。"""

    payload = b"png-bytes"
    ocr = _RecordingImageOcr({payload: "监控工具：报文统计信息，统计各实体的数据收发数量"})
    parser = PptxParser(image_ocr=ocr, ocr_language="chi_sim+eng")

    segments = await parser.parse(
        "deck.pptx",
        _pptx_with_slide_images(("ppt/media/image1.png", payload, "../media/image1.png")),
    )

    assert [segment.locator.page_number for segment in segments] == [1, 2]
    # 夹具刻意让声明顺序与文档顺序不同：第 1 页是 "First declared slide"。
    assert "First declared slide" in segments[0].text
    assert "图片文字：" in segments[0].text
    assert "报文统计信息" in segments[0].text
    assert segments[0].metadata["slide_image_text"] == "true"
    # 同一张截图被两页复用，只识别一次。
    assert len(ocr.calls) == 1 and ocr.calls[0][1] == ".png"
    assert "图片文字：" in segments[1].text


@pytest.mark.asyncio
async def test_pptx_parser_skips_vector_images_and_failed_ocr() -> None:
    """矢量素材不送 OCR，识别异常也不能让整份演示文稿摄取失败。"""

    class _BrokenOcr(_RecordingImageOcr):
        async def extract(
            self,
            content: bytes,
            *,
            suffix: str,
            language: str,
            timeout_seconds: float,
        ) -> str:
            del content, suffix, language, timeout_seconds
            raise RuntimeError("tesseract crashed")

    parser = PptxParser(image_ocr=_BrokenOcr())
    source = _pptx_with_slide_images(
        ("ppt/media/image1.emf", b"emf-bytes", "../media/image1.emf"),
        ("ppt/media/image2.png", b"png-bytes", "../media/image2.png"),
    )

    try:
        segments = await parser.parse("deck.pptx", source)
    except RuntimeError as error:  # pragma: no cover - 识别失败必须被吞掉
        pytest.fail(f"图片识别失败不应中断摄取：{error}")

    assert [segment.text for segment in segments] == [
        "First declared slide",
        "Second declared slide",
    ]
    assert all("slide_image_text" not in segment.metadata for segment in segments)


@pytest.mark.asyncio
async def test_pptx_parser_honours_image_limits_without_ocr_engine() -> None:
    """未启用引擎或关闭图片识别时不产生额外文字。"""

    payload = b"png-bytes"
    source = _pptx_with_slide_images(("ppt/media/image1.png", payload, "../media/image1.png"))

    without_engine = await PptxParser().parse("deck.pptx", source)
    assert all("图片文字：" not in segment.text for segment in without_engine)

    limited = PptxParser(image_ocr=_RecordingImageOcr(), ocr_max_images_per_slide=0)
    disabled = await limited.parse("deck.pptx", source)
    assert all("图片文字：" not in segment.text for segment in disabled)

    oversized = PptxParser(image_ocr=_RecordingImageOcr(), ocr_max_image_bytes=4)
    assert all(
        "图片文字：" not in segment.text for segment in await oversized.parse("deck.pptx", source)
    )


def test_normalize_image_text_drops_layout_noise() -> None:
    """OCR 结果去掉排版空白与纯符号行，保留可检索文字。"""

    from rag_mvp.adapters.parsers.image_ocr import normalize_image_text

    assert normalize_image_text("  报文   统计信息  \n\n\n---\n\n<>\n统计项  INFO_REPLY\n") == (
        "报文 统计信息\n统计项 INFO_REPLY"
    )
