"""确定性字符长度切块：优先在换行或空格边界截断以保留可读性。"""

from __future__ import annotations

import re
from collections.abc import Sequence

from rag_mvp.domain.models import Locator
from rag_mvp.domain.textnoise import is_navigation_index
from rag_mvp.ports.chunker import ChunkDraft
from rag_mvp.ports.parser import ParsedSegment

_SENTENCE_END = re.compile(r"[。！？!?；;.]\s*|\n+")
_PROCEDURE_STEP = re.compile(
    r"(?:^|\n)\s*(?:第\s*(?P<chinese>[零〇一二三四五六七八九十百两\d]+)\s*步|"
    r"步骤\s*(?P<number>\d+)|step\s*(?P<english>\d+))\s*[:：、.)]?",
    re.IGNORECASE,
)
_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


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
        for segment in _coalesce_procedure_segments(segments):
            # 目录页只重复章节标题与页码：作为证据没有信息量，还会挤占引用编号。
            if is_navigation_index(segment.text):
                continue
            start = 0
            while start < len(segment.text):
                end = self._find_end(segment.text, start)
                body = segment.text[start:end]
                if body:
                    drafts.append(
                        ChunkDraft(
                            ordinal=len(drafts),
                            content_with_weight=self._content_with_weight(segment, body),
                            locator=self._locator(segment, start, end),
                            metadata=segment.metadata,
                        )
                    )
                if end >= len(segment.text):
                    break
                start = max(start + 1, end - self._overlap)
        return tuple(drafts)

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
        # A procedure segment may be assembled from several visual blocks. Its
        # synthetic blank lines do not correspond one-to-one with source lines, so
        # every derived chunk cites the conservative full procedure range.
        if segment.metadata.get("layout_type") == "procedure":
            return locator
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


def _coalesce_procedure_segments(
    segments: Sequence[ParsedSegment],
) -> tuple[ParsedSegment, ...]:
    """Merge adjacent explicit steps without crossing structural/source boundaries."""

    output: list[ParsedSegment] = []
    run: list[ParsedSegment] = []
    run_scope: tuple[str, ...] | None = None
    run_start = 0
    run_end = 0

    def flush() -> None:
        nonlocal run, run_scope, run_start, run_end
        if not run:
            return
        output.append(_procedure_segment(run, run_start, run_end))
        run = []
        run_scope = None
        run_start = 0
        run_end = 0

    for segment in segments:
        steps = _procedure_steps(segment)
        if not steps:
            flush()
            output.append(segment)
            continue

        scope = _procedure_scope(segment)
        start, end = steps[0], steps[-1]
        if run and (scope != run_scope or start != run_end + 1):
            flush()
        if not run:
            run_scope = scope
            run_start = start
        run.append(segment)
        run_end = end
    flush()
    return tuple(output)


def _procedure_steps(segment: ParsedSegment) -> tuple[int, ...]:
    # CHI is a keyword/topic sidecar rather than document prose. Treating its index
    # labels as procedural content would pollute the CHM reference bridge.
    if segment.metadata.get("source_type") == "chi":
        return ()
    numbers: list[int] = []
    for match in _PROCEDURE_STEP.finditer(_procedure_body(segment)):
        raw = match.group("chinese") or match.group("number") or match.group("english")
        number = _parse_step_number(raw)
        if number is not None and (not numbers or number != numbers[-1]):
            numbers.append(number)
    return tuple(numbers)


def _parse_step_number(raw: str) -> int | None:
    if raw.isdigit():
        number = int(raw)
        return number if number > 0 else None
    if raw == "十":
        return 10
    if "百" in raw:
        # Procedure labels above 99 are rare; reject rather than guessing at an
        # incomplete Chinese numeral parser and accidentally merging unrelated text.
        return None
    if "十" in raw:
        tens, ones = raw.split("十", 1)
        tens_value = _CHINESE_DIGITS.get(tens, 1) if tens else 1
        ones_value = _CHINESE_DIGITS.get(ones, 0) if ones else 0
        number = tens_value * 10 + ones_value
        return number if number > 0 else None
    digit_value = _CHINESE_DIGITS.get(raw)
    return digit_value if digit_value and digit_value > 0 else None


def _procedure_scope(segment: ParsedSegment) -> tuple[str, ...]:
    metadata = segment.metadata
    page = str(segment.locator.page_number or "")
    return (
        metadata.get("source_type", ""),
        metadata.get("topic_path", ""),
        metadata.get("heading_path", ""),
        metadata.get("section", ""),
        segment.locator.symbol or "",
        page,
    )


def _procedure_body(segment: ParsedSegment) -> str:
    text = segment.text.strip()
    heading = segment.metadata.get("heading_path", "").strip()
    prefix = f"{heading}\n\n"
    if heading and text.startswith(prefix):
        return text[len(prefix) :].strip()
    return text


def _procedure_segment(
    segments: Sequence[ParsedSegment],
    start_step: int,
    end_step: int,
) -> ParsedSegment:
    first = segments[0]
    heading = first.metadata.get("heading_path", "").strip()
    bodies = [_procedure_body(segment) for segment in segments]
    body = "\n\n".join(part for part in bodies if part)
    text = f"{heading}\n\n{body}" if heading and body else body or first.text
    line_starts = [
        segment.locator.start_line for segment in segments if segment.locator.start_line is not None
    ]
    line_ends = [
        segment.locator.end_line for segment in segments if segment.locator.end_line is not None
    ]
    scope_name = (
        first.metadata.get("topic_path")
        or first.metadata.get("heading_path")
        or first.metadata.get("section")
        or first.locator.symbol
        or "document"
    )
    page = first.locator.page_number or 0
    metadata = {
        **first.metadata,
        "layout_type": "procedure",
        "procedure_id": f"{scope_name}#page-{page}-step-{start_step}",
        "procedure_step_start": str(start_step),
        "procedure_step_end": str(end_step),
        "procedure_step_count": str(end_step - start_step + 1),
    }
    locator_metadata = {
        **first.locator.metadata,
        "procedure_step_start": str(start_step),
        "procedure_step_end": str(end_step),
    }
    return ParsedSegment(
        text=text,
        locator=Locator(
            page_number=first.locator.page_number,
            start_line=min(line_starts) if line_starts else None,
            end_line=max(line_ends) if line_ends else None,
            symbol=first.locator.symbol,
            language=first.locator.language,
            metadata=locator_metadata,
        ),
        metadata=metadata,
    )
