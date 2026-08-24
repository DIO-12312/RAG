"""Deterministic character-bounded chunking with separator preference."""

from __future__ import annotations

from collections.abc import Sequence

from rag_mvp.domain.models import Locator
from rag_mvp.ports.chunker import ChunkDraft
from rag_mvp.ports.parser import ParsedSegment


class RecursiveChunker:
    """Split segments with stable overlap while preferring line and word boundaries."""

    def __init__(self, chunk_size: int, overlap: int) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be at least 1")
        if overlap < 0 or overlap >= chunk_size:
            raise ValueError("overlap must be non-negative and smaller than chunk_size")
        self._chunk_size = chunk_size
        self._overlap = overlap

    async def split(self, segments: Sequence[ParsedSegment]) -> tuple[ChunkDraft, ...]:
        drafts: list[ChunkDraft] = []
        for segment in segments:
            start = 0
            while start < len(segment.text):
                end = self._find_end(segment.text, start)
                content = segment.text[start:end]
                if content:
                    drafts.append(
                        ChunkDraft(
                            ordinal=len(drafts),
                            content_with_weight=content,
                            locator=self._locator(segment, start, end),
                            metadata=segment.metadata,
                        )
                    )
                if end >= len(segment.text):
                    break
                start = max(start + 1, end - self._overlap)
        return tuple(drafts)

    def _find_end(self, text: str, start: int) -> int:
        hard_end = min(start + self._chunk_size, len(text))
        if hard_end == len(text):
            return hard_end

        minimum = start + self._overlap + 1
        newline = text.rfind("\n", minimum, hard_end + 1)
        space = text.rfind(" ", minimum, hard_end + 1)
        boundary = max(newline, space)
        return boundary + 1 if boundary >= minimum else hard_end

    @staticmethod
    def _locator(segment: ParsedSegment, start: int, end: int) -> Locator:
        locator = segment.locator
        start_line = locator.start_line
        end_line = locator.end_line
        if locator.start_line is not None:
            start_line = locator.start_line + segment.text.count("\n", 0, start)
            end_line = locator.start_line + segment.text.count("\n", 0, end)
            if end > start and segment.text[end - 1] == "\n":
                end_line -= 1
        return Locator(
            page_number=locator.page_number,
            start_line=start_line,
            end_line=end_line,
            symbol=locator.symbol,
            language=locator.language,
            metadata=locator.metadata,
        )
