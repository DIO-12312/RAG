"""文档切块能力边界：输入可追溯片段，输出具有稳定顺序的 Chunk 草稿。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from rag_mvp.domain.models import Locator
from rag_mvp.ports.parser import ParsedSegment


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    ordinal: int
    content_with_weight: str
    locator: Locator
    metadata: Mapping[str, str] = field(default_factory=dict)


class Chunker(Protocol):
    """Split normalized content into stable evidence chunks."""

    # 实现 split 对应的局部职责。
    async def split(self, segments: Sequence[ParsedSegment]) -> Sequence[ChunkDraft]: ...
