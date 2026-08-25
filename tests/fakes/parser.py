"""Whole-document parser used only by focused tests."""

from rag_mvp.domain.models import Locator
from rag_mvp.ports.parser import ParsedSegment


class FakeParser:
    async def parse(self, source_name: str, content: bytes) -> tuple[ParsedSegment, ...]:
        del source_name
        return (
            ParsedSegment(
                text=content.decode("utf-8"),
                locator=Locator(start_line=1, end_line=max(content.count(b"\n") + 1, 1)),
            ),
        )
