"""Text PDF parser preserving page-level provenance."""

from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader

from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.domain.models import Locator
from rag_mvp.ports.parser import ParsedSegment


class PdfParser:
    async def parse(self, source_name: str, content: bytes) -> tuple[ParsedSegment, ...]:
        del source_name
        try:
            reader = PdfReader(BytesIO(content))
            segments = []
            for page_number, page in enumerate(reader.pages, start=1):
                text = (page.extract_text() or "").replace("\r\n", "\n").replace("\r", "\n")
                normalized = text.strip()
                if not normalized:
                    continue
                segments.append(
                    ParsedSegment(
                        text=normalized,
                        locator=Locator(
                            page_number=page_number,
                            start_line=1,
                            end_line=normalized.count("\n") + 1,
                        ),
                        metadata={"source_type": "pdf"},
                    )
                )
        except Exception as error:
            raise DomainError(
                DomainFailure("INVALID_PDF", "source is not a readable text PDF")
            ) from error
        return tuple(segments)
