"""轻量多语言代码解析器：保留顶层符号，方便代码检索定位。"""

from __future__ import annotations

import re
from pathlib import Path

from rag_mvp.adapters.parsers.text import TextParser
from rag_mvp.domain.models import Locator
from rag_mvp.ports.parser import ParsedSegment

_LANGUAGES = {
    ".py": "python",
    ".go": "go",
    ".js": "javascript",
    ".ts": "typescript",
    ".java": "java",
}
_SYMBOLS = {
    "python": re.compile(r"^(?:class|def|async\s+def)\s+([A-Za-z_]\w*)"),
    "go": re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"),
    "javascript": re.compile(
        r"^(?:export\s+)?(?:(?:async\s+)?function|class)\s+([A-Za-z_$][\w$]*)"
    ),
    "typescript": re.compile(
        r"^(?:export\s+)?(?:(?:async\s+)?function|class|interface|enum)\s+"
        r"([A-Za-z_$][\w$]*)"
    ),
    "java": re.compile(
        r"^(?:(?:public|protected|private|abstract|final|static)\s+)*"
        r"(?:class|interface|enum|record)\s+([A-Za-z_]\w*)"
    ),
}


class CodeParser:
    # 实现 parse 对应的局部职责。
    async def parse(self, source_name: str, content: bytes) -> tuple[ParsedSegment, ...]:
        language = _LANGUAGES.get(Path(source_name).suffix.casefold())
        if language is None:
            language = "text"
        normalized = (await TextParser().parse(source_name, content))[0].text
        lines = normalized.splitlines()
        pattern = _SYMBOLS.get(language)
        symbols = [] if pattern is None else self._find_symbols(lines, pattern)
        if not symbols:
            return self._build(lines, language, ((0, len(lines), None),))

        ranges: list[tuple[int, int, str | None]] = []
        if symbols[0][0] > 0:
            ranges.append((0, symbols[0][0], None))
        for position, (start, symbol) in enumerate(symbols):
            end = symbols[position + 1][0] if position + 1 < len(symbols) else len(lines)
            ranges.append((start, end, symbol))
        return self._build(lines, language, tuple(ranges))

    @staticmethod
    # 内部辅助：完成 find_symbols 所需的局部转换或校验。
    def _find_symbols(lines: list[str], pattern: re.Pattern[str]) -> list[tuple[int, str]]:
        result: list[tuple[int, str]] = []
        for index, line in enumerate(lines):
            match = pattern.match(line)
            if match is not None:
                result.append((index, match.group(1)))
        return result

    @staticmethod
    # 内部辅助：完成 build 所需的局部转换或校验。
    def _build(
        lines: list[str],
        language: str,
        ranges: tuple[tuple[int, int, str | None], ...],
    ) -> tuple[ParsedSegment, ...]:
        segments: list[ParsedSegment] = []
        for raw_start, raw_end, symbol in ranges:
            start = raw_start
            end = raw_end
            while start < end and not lines[start].strip():
                start += 1
            while end > start and not lines[end - 1].strip():
                end -= 1
            if start == end:
                continue
            segments.append(
                ParsedSegment(
                    text="\n".join(lines[start:end]),
                    locator=Locator(
                        start_line=start + 1,
                        end_line=end,
                        symbol=symbol,
                        language=language,
                    ),
                    metadata={"source_type": "code", "language": language},
                )
            )
        return tuple(segments)
