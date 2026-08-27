"""确定性字符长度切块：优先在换行或空格边界截断以保留可读性。"""

from __future__ import annotations

from collections.abc import Sequence

from rag_mvp.domain.models import Locator
from rag_mvp.ports.chunker import ChunkDraft
from rag_mvp.ports.parser import ParsedSegment


class RecursiveChunker:
    """Split segments with stable overlap while preferring line and word boundaries."""

    # 初始化该对象的依赖、配置或受控资源。
    def __init__(self, chunk_size: int, overlap: int) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be at least 1")
        if overlap < 0 or overlap >= chunk_size:
            raise ValueError("overlap must be non-negative and smaller than chunk_size")
        self._chunk_size = chunk_size
        self._overlap = overlap

    # 实现 split 对应的局部职责。
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

    # 内部辅助：完成 find_end 所需的局部转换或校验。
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
    # 内部辅助：完成 locator 所需的局部转换或校验。
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
