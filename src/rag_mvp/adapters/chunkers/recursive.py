"""确定性字符长度切块：优先在换行或空格边界截断以保留可读性。"""

from __future__ import annotations

import re
from collections.abc import Sequence

from rag_mvp.domain.models import Locator
from rag_mvp.ports.chunker import ChunkDraft
from rag_mvp.ports.parser import ParsedSegment

_SENTENCE_END = re.compile(r"[。！？!?；;.]\s*|\n+")


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
            slices = self._segment_slices(segment.text)
            for chunk_index, (start, end) in enumerate(slices):
                body = segment.text[start:end]
                metadata = dict(segment.metadata)
                if metadata.get("source_type") == "chm":
                    metadata.update(
                        {
                            "chunk_role": "section_child",
                            "chunk_index_in_section": str(chunk_index),
                            "section_chunk_count": str(len(slices)),
                        }
                    )
                drafts.append(
                    ChunkDraft(
                        ordinal=len(drafts),
                        content_with_weight=self._content_with_weight(segment, body),
                        locator=self._locator(segment, start, end),
                        metadata=metadata,
                    )
                )
        return tuple(drafts)

    def _segment_slices(self, text: str) -> tuple[tuple[int, int], ...]:
        slices: list[tuple[int, int]] = []
        start = 0
        while start < len(text):
            end = self._find_end(text, start)
            if text[start:end]:
                slices.append((start, end))
            if end >= len(text):
                break
            start = max(start + 1, end - self._overlap)
        return tuple(slices)

    @staticmethod
    def _content_with_weight(segment: ParsedSegment, body: str) -> str:
        source_type = segment.metadata.get("source_type")
        if source_type not in {"chm", "chi"}:
            return body
        context: tuple[tuple[str, str | None], ...]
        if source_type == "chm":
            context = (
                ("Topic", segment.metadata.get("topic_title")),
                ("Heading", segment.metadata.get("heading_path")),
                ("Symbol", segment.locator.symbol),
            )
        else:
            context = (
                ("Index keyword", segment.locator.symbol),
                ("Topic", segment.metadata.get("topic_title")),
                ("Topic path", segment.metadata.get("topic_path")),
                ("Topic URL", segment.metadata.get("topic_url")),
                ("Associated CHM", segment.metadata.get("associated_chm_source_name")),
            )
        prefix = "\n".join(
            f"{label}: {value.strip()}" for label, value in context if value and value.strip()
        )
        return f"{prefix}\n\n{body}" if prefix else body

    # 内部辅助：完成 find_end 所需的局部转换或校验。
    def _find_end(self, text: str, start: int) -> int:
        hard_end = min(start + self._chunk_size, len(text))
        if hard_end == len(text):
            return hard_end

        minimum = start + self._overlap + 1
        paragraph = text.rfind("\n\n", minimum, hard_end + 1)
        if paragraph >= minimum:
            return paragraph + 2

        sentence_end = -1
        for match in _SENTENCE_END.finditer(text, minimum, hard_end + 1):
            sentence_end = match.end()
        if sentence_end > minimum:
            return sentence_end

        # Whitespace is the last language-neutral lexical/token boundary. A single
        # unbreakable token is hard-sliced so the configured upper bound still holds.
        token_boundary = max(
            text.rfind(" ", minimum, hard_end + 1),
            text.rfind("\t", minimum, hard_end + 1),
        )
        return token_boundary + 1 if token_boundary >= minimum else hard_end

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
