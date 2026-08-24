"""One-segment-per-chunk splitter used only by port contract tests."""

from collections.abc import Sequence

from rag_mvp.ports.chunker import ChunkDraft
from rag_mvp.ports.parser import ParsedSegment


class FakeChunker:
    async def split(self, segments: Sequence[ParsedSegment]) -> tuple[ChunkDraft, ...]:
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
