"""Document chunker capability boundary."""

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

    async def split(self, segments: Sequence[ParsedSegment]) -> Sequence[ChunkDraft]: ...
