"""Markdown parser preserving heading provenance and source lines."""

from __future__ import annotations

import re

from rag_mvp.adapters.parsers.text import TextParser
from rag_mvp.domain.models import Locator
from rag_mvp.ports.parser import ParsedSegment

_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")


class MarkdownParser:
    async def parse(self, source_name: str, content: bytes) -> tuple[ParsedSegment, ...]:
        normalized = (await TextParser().parse(source_name, content))[0].text
        lines = normalized.splitlines()
        headings = [index for index, line in enumerate(lines) if _HEADING.match(line)]
        if not headings:
            return self._segments(lines, ((0, len(lines), None),))

        ranges: list[tuple[int, int, str | None]] = []
        if headings[0] > 0:
            ranges.append((0, headings[0], None))
        for position, start in enumerate(headings):
            end = headings[position + 1] if position + 1 < len(headings) else len(lines)
            match = _HEADING.match(lines[start])
            ranges.append((start, end, match.group(1).strip() if match else None))
        return self._segments(lines, tuple(ranges))

    @staticmethod
    def _segments(
        lines: list[str], ranges: tuple[tuple[int, int, str | None], ...]
    ) -> tuple[ParsedSegment, ...]:
        segments: list[ParsedSegment] = []
        for raw_start, raw_end, section in ranges:
            start = raw_start
            end = raw_end
            while start < end and not lines[start].strip():
                start += 1
            while end > start and not lines[end - 1].strip():
                end -= 1
            if start == end:
                continue
            metadata = {"source_type": "markdown"}
            if section is not None:
                metadata["section"] = section
            segments.append(
                ParsedSegment(
                    text="\n".join(lines[start:end]),
                    locator=Locator(start_line=start + 1, end_line=end),
                    metadata=metadata,
                )
            )
        return tuple(segments)
