"""按源文件扩展名路由到受支持解析器，未知格式在边界处明确失败。"""

from __future__ import annotations

from pathlib import Path

from rag_mvp.adapters.parsers.chi import ChiParser
from rag_mvp.adapters.parsers.chm import ChmLibExtractor, ChmParser
from rag_mvp.adapters.parsers.code import CodeParser
from rag_mvp.adapters.parsers.markdown import MarkdownParser
from rag_mvp.adapters.parsers.pdf import PdfParser
from rag_mvp.adapters.parsers.text import TextParser
from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.ports.parser import ParsedSegment, Parser, PdfParserMode


class SourceParserRouter:
    # 初始化该对象的依赖、配置或受控资源。
    def __init__(
        self,
        *,
        chm_extractor_path: str = "extract_chmLib",
        chm_extract_timeout_seconds: float = 30.0,
        chm_max_files: int = 8192,
        chm_max_topics: int = 4096,
        chm_max_expanded_bytes: int = 128 * 1024 * 1024,
        pdf_parser_mode: PdfParserMode = PdfParserMode.AUTO,
        pdf_native_text_min_chars_per_page: int = 40,
        pdf_ocr_language: str = "chi_sim+eng",
        pdf_ocr_dpi: int = 200,
        pdf_ocr_timeout_seconds: float = 60.0,
        pdf_max_pages: int = 1000,
        pdf_header_footer_margin_ratio: float = 0.12,
        pdf_repeated_margin_min_pages: int = 3,
        chm_parser: Parser | None = None,
        chi_parser: Parser | None = None,
        pdf_parser: Parser | None = None,
    ) -> None:
        text = TextParser()
        markdown = MarkdownParser()
        code = CodeParser()
        pdf = pdf_parser or PdfParser(
            mode=pdf_parser_mode,
            native_text_min_chars_per_page=pdf_native_text_min_chars_per_page,
            ocr_language=pdf_ocr_language,
            ocr_dpi=pdf_ocr_dpi,
            ocr_timeout_seconds=pdf_ocr_timeout_seconds,
            max_pages=pdf_max_pages,
            header_footer_margin_ratio=pdf_header_footer_margin_ratio,
            repeated_margin_min_pages=pdf_repeated_margin_min_pages,
        )
        extractor = ChmLibExtractor(
            executable=chm_extractor_path,
            timeout_seconds=chm_extract_timeout_seconds,
            max_files=chm_max_files,
            max_expanded_bytes=chm_max_expanded_bytes,
        )
        chm = chm_parser or ChmParser(
            extractor,
            max_topics=chm_max_topics,
        )
        chi = chi_parser or ChiParser(extractor)
        self._parsers: dict[str, Parser] = {
            ".txt": text,
            ".md": markdown,
            ".py": code,
            ".go": code,
            ".js": code,
            ".ts": code,
            ".java": code,
            ".pdf": pdf,
            ".chm": chm,
            ".chi": chi,
        }

    # 实现 parse 对应的局部职责。
    async def parse(self, source_name: str, content: bytes) -> tuple[ParsedSegment, ...]:
        suffix = Path(source_name).suffix.casefold()
        parser = self._parsers.get(suffix)
        if parser is None:
            raise DomainError(
                DomainFailure(
                    "UNSUPPORTED_SOURCE_TYPE",
                    f"source extension {suffix or '<none>'} is not supported",
                )
            )
        return tuple(await parser.parse(source_name, content))
