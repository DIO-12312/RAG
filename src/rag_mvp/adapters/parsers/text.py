"""UTF-8 plain-text parser with deterministic newline normalization."""

from __future__ import annotations

from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.domain.models import Locator
from rag_mvp.ports.parser import ParsedSegment


class TextParser:
    """Parse a complete UTF-8 text object into one traceable segment."""

    async def parse(self, source_name: str, content: bytes) -> tuple[ParsedSegment, ...]:
        del source_name
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise DomainError(
                DomainFailure(
                    code="INVALID_UTF8",
                    message="plain-text source is not valid UTF-8",
                    retryable=False,
                )
            ) from error

        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        return (
            ParsedSegment(
                text=normalized,
                locator=Locator(start_line=1, end_line=normalized.count("\n") + 1),
                metadata={"source_type": "text"},
            ),
        )
