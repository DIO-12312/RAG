"""UTF-8 纯文本解析器：先确定性规范化换行，再生成可追溯文本片段。"""

from __future__ import annotations

from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.domain.models import Locator
from rag_mvp.ports.parser import ParsedSegment


class TextParser:
    """Parse a complete UTF-8 text object into one traceable segment."""

    # 实现 parse 对应的局部职责。
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
