"""Document parser capability boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from rag_mvp.domain.models import Locator


@dataclass(frozen=True, slots=True)
class ParsedSegment:
    text: str
    locator: Locator
    metadata: Mapping[str, str] = field(default_factory=dict)


class Parser(Protocol):
    """Parse source objects into normalized, traceable content."""

    async def parse(self, source_name: str, content: bytes) -> Sequence[ParsedSegment]: ...
