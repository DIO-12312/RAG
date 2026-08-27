"""文档解析能力边界：将源字节转换为带出处定位的规范化片段。"""

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

    # 实现 parse 对应的局部职责。
    async def parse(self, source_name: str, content: bytes) -> Sequence[ParsedSegment]: ...
