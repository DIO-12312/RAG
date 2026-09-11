"""PDF parser with a light path and an opt-in DeepDoc-style structured path."""

from __future__ import annotations

import asyncio
import csv
import math
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from io import BytesIO, StringIO
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from pypdf import PdfReader

from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.domain.models import Locator
from rag_mvp.ports.parser import ParsedSegment, PdfParserMode

_DIGITS = re.compile(r"\d+")
_DOT_LEADER = re.compile(r"[.…·]{4,}")
_TRIVIAL_HEADING = re.compile(r"^(?:[ivxlcdm]+|\d+|task\s+\d+)$", re.IGNORECASE)
_LIST_PREFIX = re.compile(r"^(?:[-*•]|\d+[.)]|[（(]?[一二三四五六七八九十]+[）)])\s*")
_SENTENCE_END = re.compile(r"[。！？!?；;.]$")


@dataclass(frozen=True, slots=True)
class PdfLayoutLine:
    """One ordered PDF line in top-left page coordinates measured in points."""

    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    font_size: float
    order: int = 1
    column_count: int = 1
    bold: bool = False
    confidence: float | None = None
    extraction_method: str = "native"


class PdfOcrEngine(Protocol):
    """OCR boundary kept inside the parser adapter."""

    def available(self) -> bool: ...

    def extract(
        self,
        content: bytes,
        *,
        page_number: int,
        page_width: float,
        page_height: float,
        dpi: int,
        language: str,
        timeout_seconds: float,
    ) -> tuple[PdfLayoutLine, ...]: ...


class TesseractPdfOcrEngine:
    """Render one PDF page with Poppler and read Tesseract TSV output."""

    def __init__(self, renderer: str = "pdftoppm", executable: str = "tesseract") -> None:
        self._renderer = renderer
        self._executable = executable

    def available(self) -> bool:
        return (
            shutil.which(self._renderer) is not None and shutil.which(self._executable) is not None
        )

    def extract(
        self,
        content: bytes,
        *,
        page_number: int,
        page_width: float,
        page_height: float,
        dpi: int,
        language: str,
        timeout_seconds: float,
    ) -> tuple[PdfLayoutLine, ...]:
        del page_width, page_height
        if not self.available():
            raise DomainError(
                DomainFailure(
                    "PDF_OCR_UNAVAILABLE",
                    "scanned PDF requires pdftoppm and tesseract",
                )
            )
        try:
            with TemporaryDirectory(prefix="rag-pdf-ocr-") as temporary:
                root = Path(temporary)
                source = root / "source.pdf"
                target = root / "page"
                source.write_bytes(content)
                rendered = subprocess.run(
                    [
                        self._renderer,
                        "-f",
                        str(page_number),
                        "-l",
                        str(page_number),
                        "-r",
                        str(dpi),
                        "-png",
                        "-singlefile",
                        str(source),
                        str(target),
                    ],
                    check=False,
                    capture_output=True,
                    timeout=timeout_seconds,
                )
                image = target.with_suffix(".png")
                if rendered.returncode != 0 or not image.is_file():
                    raise DomainError(
                        DomainFailure("PDF_RENDER_FAILED", "PDF page rendering failed")
                    )
                recognized = subprocess.run(
                    [
                        self._executable,
                        str(image),
                        "stdout",
                        "-l",
                        language,
                        "tsv",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                )
                if recognized.returncode != 0:
                    raise DomainError(
                        DomainFailure("PDF_OCR_FAILED", "Tesseract could not recognize PDF page")
                    )
                return _lines_from_tesseract_tsv(recognized.stdout, dpi)
        except subprocess.TimeoutExpired as error:
            raise DomainError(
                DomainFailure(
                    "PDF_OCR_TIMEOUT",
                    "PDF OCR exceeded its configured deadline",
                    retryable=True,
                )
            ) from error


@dataclass(frozen=True, slots=True)
class _PageLayout:
    number: int
    width: float
    height: float
    lines: tuple[PdfLayoutLine, ...]


@dataclass(frozen=True, slots=True)
class _TextFragment:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    font_size: float
    bold: bool


class PdfParser:
    """Parse text PDFs structurally and OCR only pages without usable native text."""

    def __init__(
        self,
        *,
        mode: PdfParserMode = PdfParserMode.AUTO,
        native_text_min_chars_per_page: int = 40,
        ocr_language: str = "chi_sim+eng",
        ocr_dpi: int = 200,
        ocr_timeout_seconds: float = 60.0,
        max_pages: int = 1000,
        header_footer_margin_ratio: float = 0.12,
        repeated_margin_min_pages: int = 3,
        ocr_engine: PdfOcrEngine | None = None,
    ) -> None:
        self._mode = mode
        self._native_text_min_chars = native_text_min_chars_per_page
        self._ocr_language = ocr_language
        self._ocr_dpi = ocr_dpi
        self._ocr_timeout_seconds = ocr_timeout_seconds
        self._max_pages = max_pages
        self._margin_ratio = header_footer_margin_ratio
        self._repeated_margin_min_pages = repeated_margin_min_pages
        self._ocr = ocr_engine or TesseractPdfOcrEngine()

    async def parse(self, source_name: str, content: bytes) -> tuple[ParsedSegment, ...]:
        return await asyncio.to_thread(self._parse_sync, source_name, content)

    def _parse_sync(self, source_name: str, content: bytes) -> tuple[ParsedSegment, ...]:
        try:
            reader = PdfReader(BytesIO(content))
            if len(reader.pages) > self._max_pages:
                raise DomainError(
                    DomainFailure(
                        "PDF_PAGE_LIMIT_EXCEEDED",
                        f"PDF contains more than {self._max_pages} pages",
                    )
                )
            if self._mode is PdfParserMode.PLAIN:
                return self._parse_plain(reader)
            return self._parse_structured(source_name, content, reader)
        except DomainError:
            raise
        except Exception as error:
            raise DomainError(
                DomainFailure("INVALID_PDF", "source is not a readable PDF")
            ) from error

    @staticmethod
    def _parse_plain(reader: PdfReader) -> tuple[ParsedSegment, ...]:
        segments: list[ParsedSegment] = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").replace("\r\n", "\n").replace("\r", "\n")
            normalized = text.strip()
            if not normalized:
                continue
            segments.append(
                ParsedSegment(
                    text=normalized,
                    locator=Locator(
                        page_number=page_number,
                        start_line=1,
                        end_line=normalized.count("\n") + 1,
                    ),
                    metadata={
                        "source_type": "pdf",
                        "parser_mode": PdfParserMode.PLAIN.value,
                        "extraction_method": "native",
                        "layout_type": "page",
                    },
                )
            )
        return tuple(segments)

    def _parse_structured(
        self,
        source_name: str,
        content: bytes,
        reader: PdfReader,
    ) -> tuple[ParsedSegment, ...]:
        pages: list[_PageLayout] = []
        for page_number, page in enumerate(reader.pages, start=1):
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
            native_lines = _extract_native_lines(page, width, height)
            native_chars = sum(len(line.text.replace("\t", "")) for line in native_lines)
            lines = native_lines
            if native_chars < self._native_text_min_chars:
                if self._ocr.available():
                    lines = self._ocr.extract(
                        content,
                        page_number=page_number,
                        page_width=width,
                        page_height=height,
                        dpi=self._ocr_dpi,
                        language=self._ocr_language,
                        timeout_seconds=self._ocr_timeout_seconds,
                    )
                elif self._mode is PdfParserMode.DEEPDOC:
                    raise DomainError(
                        DomainFailure(
                            "PDF_OCR_UNAVAILABLE",
                            "scanned PDF requires pdftoppm and tesseract",
                        )
                    )
            pages.append(
                _PageLayout(
                    number=page_number,
                    width=width,
                    height=height,
                    lines=_order_page_lines(lines, width),
                )
            )

        repeated = _repeated_margin_keys(
            pages,
            margin_ratio=self._margin_ratio,
            minimum_pages=self._repeated_margin_min_pages,
        )
        document_title = _document_title(reader, source_name)
        heading_stack: dict[int, str] = {}
        segments: list[ParsedSegment] = []
        for layout_page in pages:
            body_size = (
                median(line.font_size for line in layout_page.lines) if layout_page.lines else 1.0
            )
            visible = tuple(
                line
                for line in layout_page.lines
                if _margin_key(
                    line,
                    layout_page.height,
                    self._margin_ratio,
                    line_count=len(layout_page.lines),
                    body_size=body_size,
                )
                not in repeated
            )
            page_segments, heading_stack = _page_segments(
                layout_page,
                visible,
                document_title=document_title,
                parser_mode=self._mode,
                heading_stack=heading_stack,
            )
            segments.extend(page_segments)
        return tuple(segments)


def _document_title(reader: PdfReader, source_name: str) -> str:
    metadata = reader.metadata
    title = getattr(metadata, "title", None) if metadata is not None else None
    normalized = title.strip() if isinstance(title, str) else ""
    if normalized.casefold() in {"", "untitled", "unknown"}:
        return Path(source_name).stem
    return normalized


def _extract_native_lines(
    page: Any, page_width: float, page_height: float
) -> tuple[PdfLayoutLine, ...]:
    del page_width
    fragments: list[_TextFragment] = []

    def visit_text(
        text: str,
        current_matrix: Sequence[float],
        text_matrix: Sequence[float],
        font_dictionary: dict[str, Any] | None,
        font_size: float,
    ) -> None:
        cleaned_lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
        if not cleaned_lines:
            return
        size = max(float(font_size or 0.0), 1.0)
        x, baseline = _transform_point(text_matrix, current_matrix)
        font_name = str((font_dictionary or {}).get("/BaseFont", "")).casefold()
        bold = "bold" in font_name or "black" in font_name
        for offset, cleaned in enumerate(cleaned_lines):
            top = max(0.0, page_height - baseline - size + offset * size * 1.2)
            estimated_width = max(size * 0.45 * len(cleaned), size)
            fragments.append(
                _TextFragment(
                    text=cleaned,
                    x0=max(0.0, x),
                    top=top,
                    x1=max(0.0, x) + estimated_width,
                    bottom=min(page_height, top + size * 1.25),
                    font_size=size,
                    bold=bold,
                )
            )

    page.extract_text(visitor_text=visit_text)
    return _group_fragments(fragments)


def _transform_point(
    text_matrix: Sequence[float], current_matrix: Sequence[float]
) -> tuple[float, float]:
    if len(text_matrix) < 6 or len(current_matrix) < 6:
        return 0.0, 0.0
    x = text_matrix[4] * current_matrix[0] + text_matrix[5] * current_matrix[2]
    x += current_matrix[4]
    y = text_matrix[4] * current_matrix[1] + text_matrix[5] * current_matrix[3]
    y += current_matrix[5]
    return float(x), float(y)


def _group_fragments(fragments: Iterable[_TextFragment]) -> tuple[PdfLayoutLine, ...]:
    rows: list[list[_TextFragment]] = []
    for fragment in sorted(fragments, key=lambda item: (item.top, item.x0)):
        tolerance = max(2.0, fragment.font_size * 0.35)
        row = next(
            (
                candidate
                for candidate in reversed(rows[-4:])
                if abs(median(item.top for item in candidate) - fragment.top) <= tolerance
            ),
            None,
        )
        if row is None:
            rows.append([fragment])
        else:
            row.append(fragment)

    lines: list[PdfLayoutLine] = []
    for order, row in enumerate(rows, start=1):
        ordered = sorted(row, key=lambda item: item.x0)
        text = ordered[0].text
        wide_gaps = 0
        previous = ordered[0]
        for fragment in ordered[1:]:
            gap = fragment.x0 - previous.x1
            if gap > max(18.0, max(previous.font_size, fragment.font_size) * 2.5):
                separator = "\t"
                wide_gaps += 1
            elif _needs_space(previous.text, fragment.text, gap):
                separator = " "
            else:
                separator = ""
            text += separator + fragment.text
            previous = fragment
        lines.append(
            PdfLayoutLine(
                text=text.strip(),
                x0=min(item.x0 for item in ordered),
                top=min(item.top for item in ordered),
                x1=max(item.x1 for item in ordered),
                bottom=max(item.bottom for item in ordered),
                font_size=median(item.font_size for item in ordered),
                order=order,
                column_count=wide_gaps + 1,
                bold=any(item.bold for item in ordered),
            )
        )
    return tuple(line for line in lines if line.text)


def _needs_space(previous: str, current: str, gap: float) -> bool:
    if gap <= 0 or previous.endswith((" ", "-", "/")):
        return False
    if current.startswith((",", ".", ":", ";", ")", "]", "}", "，", "。", "：", "；")):
        return False
    return not (_is_cjk(previous[-1]) and _is_cjk(current[0]))


def _is_cjk(character: str) -> bool:
    return "\u3400" <= character <= "\u9fff"


def _order_page_lines(
    lines: Sequence[PdfLayoutLine], page_width: float
) -> tuple[PdfLayoutLine, ...]:
    vertical = sorted(lines, key=lambda line: (line.top, line.x0))
    if len(vertical) < 4:
        return _renumber(vertical)

    ordered: list[PdfLayoutLine] = []
    pending: list[PdfLayoutLine] = []
    midpoint = page_width / 2

    def flush() -> None:
        if not pending:
            return
        left = [line for line in pending if (line.x0 + line.x1) / 2 < midpoint]
        right = [line for line in pending if line not in left]
        if len(left) >= 2 and len(right) >= 2:
            ordered.extend(sorted(left, key=lambda line: (line.top, line.x0)))
            ordered.extend(sorted(right, key=lambda line: (line.top, line.x0)))
        else:
            ordered.extend(sorted(pending, key=lambda line: (line.top, line.x0)))
        pending.clear()

    for line in vertical:
        spans_middle = line.x0 < midpoint < line.x1
        if spans_middle or line.x1 - line.x0 >= page_width * 0.65:
            flush()
            ordered.append(line)
        else:
            pending.append(line)
    flush()
    return _renumber(ordered)


def _renumber(lines: Sequence[PdfLayoutLine]) -> tuple[PdfLayoutLine, ...]:
    return tuple(
        PdfLayoutLine(
            text=line.text,
            x0=line.x0,
            top=line.top,
            x1=line.x1,
            bottom=line.bottom,
            font_size=line.font_size,
            order=index,
            column_count=line.column_count,
            bold=line.bold,
            confidence=line.confidence,
            extraction_method=line.extraction_method,
        )
        for index, line in enumerate(lines, start=1)
    )


def _margin_key(
    line: PdfLayoutLine,
    page_height: float,
    margin_ratio: float,
    *,
    line_count: int,
    body_size: float,
) -> str:
    top_margin = line.top <= page_height * margin_ratio and line.order <= 2
    bottom_margin = line.bottom >= page_height * (1 - margin_ratio) and line.order >= line_count - 1
    if not top_margin and not bottom_margin:
        return ""
    if top_margin and line.font_size > body_size * 1.2:
        return ""
    normalized = _DIGITS.sub("#", " ".join(line.text.casefold().split()))
    return normalized if len(normalized) <= 160 else ""


def _repeated_margin_keys(
    pages: Sequence[_PageLayout],
    *,
    margin_ratio: float,
    minimum_pages: int,
) -> frozenset[str]:
    if len(pages) < minimum_pages:
        return frozenset()
    counts: Counter[str] = Counter()
    for page in pages:
        body_size = median(line.font_size for line in page.lines) if page.lines else 1.0
        counts.update(
            {
                key
                for line in page.lines
                if (
                    key := _margin_key(
                        line,
                        page.height,
                        margin_ratio,
                        line_count=len(page.lines),
                        body_size=body_size,
                    )
                )
            }
        )
    threshold = max(minimum_pages, math.ceil(len(pages) * 0.6))
    return frozenset(key for key, count in counts.items() if count >= threshold)


def _page_segments(
    page: _PageLayout,
    lines: Sequence[PdfLayoutLine],
    *,
    document_title: str,
    parser_mode: PdfParserMode,
    heading_stack: dict[int, str],
) -> tuple[tuple[ParsedSegment, ...], dict[int, str]]:
    if not lines:
        return (), dict(heading_stack)
    body_size = median(line.font_size for line in lines)
    table_orders = _table_line_orders(lines)
    stack = dict(heading_stack)
    segments: list[ParsedSegment] = []
    buffered: list[PdfLayoutLine] = []
    buffered_type = "paragraph"

    def flush() -> None:
        nonlocal buffered
        if not buffered:
            return
        path = " > ".join(stack[level] for level in sorted(stack))
        text = _block_text(buffered, buffered_type, path)
        confidence_values = [line.confidence for line in buffered if line.confidence is not None]
        metadata = {
            "source_type": "pdf",
            "parser_mode": parser_mode.value,
            "extraction_method": _extraction_method(buffered),
            "layout_type": buffered_type,
            "document_title": document_title,
            "heading_path": path,
            "bbox": _bbox(buffered),
            "coordinate_space": "pdf_points_top_left",
        }
        if confidence_values:
            metadata["ocr_confidence"] = f"{sum(confidence_values) / len(confidence_values):.2f}"
        segments.append(
            ParsedSegment(
                text=text,
                locator=Locator(
                    page_number=page.number,
                    start_line=min(line.order for line in buffered),
                    end_line=max(line.order for line in buffered),
                    symbol=stack[max(stack)] if stack else None,
                    metadata={
                        "bbox": metadata["bbox"],
                        "coordinate_space": metadata["coordinate_space"],
                    },
                ),
                metadata=metadata,
            )
        )
        buffered = []

    for line in lines:
        heading_level = _heading_level(line, body_size)
        if heading_level is not None:
            flush()
            stack = {level: title for level, title in stack.items() if level < heading_level}
            stack[heading_level] = line.text
            continue
        layout_type = "table" if line.order in table_orders else _body_type(line)
        gap = line.top - buffered[-1].bottom if buffered else 0.0
        if buffered and (layout_type != buffered_type or gap > max(18.0, body_size * 1.8)):
            flush()
        buffered_type = layout_type
        buffered.append(line)
    flush()
    if not segments and stack:
        title = stack[max(stack)]
        buffered = [PdfLayoutLine(title, 0, 0, page.width, body_size, body_size)]
        buffered_type = "heading"
        flush()
    return tuple(segments), stack


def _heading_level(line: PdfLayoutLine, body_size: float) -> int | None:
    text = line.text.strip()
    if (
        len(text) > 160
        or line.column_count > 1
        or _DOT_LEADER.search(text)
        or _TRIVIAL_HEADING.fullmatch(text)
        or _SENTENCE_END.search(text)
    ):
        return None
    ratio = line.font_size / max(body_size, 1.0)
    if ratio >= 1.8:
        return 1
    if ratio >= 1.5:
        return 2
    if ratio >= 1.28:
        return 3
    if ratio >= 1.12 or line.bold:
        return 4
    return None


def _table_line_orders(lines: Sequence[PdfLayoutLine]) -> frozenset[int]:
    orders: set[int] = set()
    run: list[PdfLayoutLine] = []
    for line in lines:
        if line.column_count >= 2:
            run.append(line)
            continue
        if len(run) >= 2:
            orders.update(item.order for item in run)
        run = []
    if len(run) >= 2:
        orders.update(item.order for item in run)
    return frozenset(orders)


def _body_type(line: PdfLayoutLine) -> str:
    return "list" if _LIST_PREFIX.match(line.text) else "paragraph"


def _block_text(lines: Sequence[PdfLayoutLine], layout_type: str, heading_path: str) -> str:
    if layout_type == "table":
        body = "\n".join(
            "| " + " | ".join(cell.strip() for cell in line.text.split("\t")) + " |"
            for line in lines
        )
    else:
        body = "\n".join(line.text for line in lines)
    if heading_path and not body.startswith(heading_path):
        return f"{heading_path}\n\n{body}"
    return body


def _bbox(lines: Sequence[PdfLayoutLine]) -> str:
    x0 = min(line.x0 for line in lines)
    top = min(line.top for line in lines)
    x1 = max(line.x1 for line in lines)
    bottom = max(line.bottom for line in lines)
    return f"[{x0:.2f},{top:.2f},{x1:.2f},{bottom:.2f}]"


def _extraction_method(lines: Sequence[PdfLayoutLine]) -> str:
    methods = {line.extraction_method for line in lines}
    return next(iter(methods)) if len(methods) == 1 else "mixed"


def _lines_from_tesseract_tsv(tsv: str, dpi: int) -> tuple[PdfLayoutLine, ...]:
    grouped: defaultdict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in csv.DictReader(StringIO(tsv), delimiter="\t"):
        text = (row.get("text") or "").strip()
        if text and row.get("level") == "5":
            key = (
                row.get("block_num", "0"),
                row.get("par_num", "0"),
                row.get("line_num", "0"),
            )
            grouped[key].append(row)

    scale = 72.0 / dpi
    lines: list[PdfLayoutLine] = []
    for order, words in enumerate(grouped.values(), start=1):
        ordered = sorted(words, key=lambda row: int(row.get("left") or 0))
        left = min(int(row.get("left") or 0) for row in ordered)
        top = min(int(row.get("top") or 0) for row in ordered)
        right = max(int(row.get("left") or 0) + int(row.get("width") or 0) for row in ordered)
        bottom = max(int(row.get("top") or 0) + int(row.get("height") or 0) for row in ordered)
        confidences = [float(row.get("conf") or -1) for row in ordered]
        confidences = [value for value in confidences if value >= 0]
        lines.append(
            PdfLayoutLine(
                text=" ".join(row["text"].strip() for row in ordered),
                x0=left * scale,
                top=top * scale,
                x1=right * scale,
                bottom=bottom * scale,
                font_size=max((bottom - top) * scale, 1.0),
                order=order,
                confidence=sum(confidences) / len(confidences) if confidences else None,
                extraction_method="ocr",
            )
        )
    return tuple(lines)
