"""Source extension router for the supported parser set."""

from __future__ import annotations

from pathlib import Path

from rag_mvp.adapters.parsers.code import CodeParser
from rag_mvp.adapters.parsers.markdown import MarkdownParser
from rag_mvp.adapters.parsers.pdf import PdfParser
from rag_mvp.adapters.parsers.text import TextParser
from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.ports.parser import ParsedSegment, Parser


class SourceParserRouter:
    def __init__(self) -> None:
        text = TextParser()
        markdown = MarkdownParser()
        code = CodeParser()
        pdf = PdfParser()
        self._parsers: dict[str, Parser] = {
            ".txt": text,
            ".md": markdown,
            ".py": code,
            ".go": code,
            ".js": code,
            ".ts": code,
            ".java": code,
            ".pdf": pdf,
        }

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
