"""仅用于端口契约测试的“一段一块”确定性切块器。"""

from collections.abc import Sequence

from rag_mvp.ports.chunker import ChunkDraft
from rag_mvp.ports.parser import ParsedSegment


class FakeChunker:
    async def split(self, segments: Sequence[ParsedSegment]) -> tuple[ChunkDraft, ...]:
        """模拟切块过程并保留输入片段的顺序。"""
        return tuple(
            ChunkDraft(
                ordinal=ordinal,
                content_with_weight=segment.text,
                locator=segment.locator,
                metadata=segment.metadata,
            )
            for ordinal, segment in enumerate(segments)
            if segment.text
        )
