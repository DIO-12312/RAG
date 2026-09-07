"""CHM 解析器：安全解包 HTML Topic，并保留 Topic/标题层级来源定位。"""

from __future__ import annotations

import asyncio
import codecs
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Protocol
from urllib.parse import unquote, urlsplit

from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.domain.models import Locator
from rag_mvp.ports.parser import ParsedSegment

_CHM_SIGNATURE = b"ITSF"
_HTML_SUFFIXES = {".htm", ".html", ".xhtm", ".xhtml"}
_TOC_SUFFIX = ".hhc"
_META_CHARSET = re.compile(
    rb"(?:charset\s*=\s*['\"]?\s*|<meta\s+charset\s*=\s*['\"])([-\w.]+)",
    re.IGNORECASE,
)
_WHITESPACE = re.compile(r"[\t\x0b\x0c \r\n]+")


@dataclass(frozen=True, slots=True)
class ChmEntry:
    """One normalized file extracted from a CHM archive."""

    path: str
    content: bytes


class ChmExtractor(Protocol):
    """Extract archive entries without exposing a persistent working directory."""

    async def extract(self, content: bytes) -> Sequence[ChmEntry]: ...


class ChmLibExtractor:
    """Invoke Debian's ``extract_chmLib`` in an isolated temporary directory."""

    def __init__(
        self,
        *,
        executable: str = "extract_chmLib",
        timeout_seconds: float = 30.0,
        max_files: int = 8192,
        max_expanded_bytes: int = 128 * 1024 * 1024,
    ) -> None:
        if not executable.strip():
            raise ValueError("CHM extractor executable must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("CHM extraction timeout must be positive")
        if max_files < 1:
            raise ValueError("CHM max files must be at least 1")
        if max_expanded_bytes < 1:
            raise ValueError("CHM expanded byte limit must be at least 1")
        self._executable = executable
        self._timeout_seconds = timeout_seconds
        self._max_files = max_files
        self._max_expanded_bytes = max_expanded_bytes

    async def extract(self, content: bytes) -> tuple[ChmEntry, ...]:
        return await asyncio.to_thread(self._extract_sync, content)

    def _extract_sync(self, content: bytes) -> tuple[ChmEntry, ...]:
        if not content.startswith(_CHM_SIGNATURE):
            raise _invalid_chm("source does not have a valid CHM signature")

        with TemporaryDirectory(prefix="rag-chm-") as temporary:
            root = Path(temporary)
            source = root / "source.chm"
            output = root / "extracted"
            source.write_bytes(content)
            output.mkdir()
            try:
                completed = subprocess.run(
                    [self._executable, str(source), str(output)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=self._timeout_seconds,
                )
            except FileNotFoundError as error:
                raise DomainError(
                    DomainFailure(
                        code="CHM_EXTRACTOR_UNAVAILABLE",
                        message="CHM extraction runtime is not installed",
                        retryable=False,
                    )
                ) from error
            except subprocess.TimeoutExpired as error:
                raise _invalid_chm("CHM extraction exceeded the configured timeout") from error

            if completed.returncode != 0:
                raise _invalid_chm("source is not a readable CHM archive")
            return self._read_entries(output)

    def _read_entries(self, output: Path) -> tuple[ChmEntry, ...]:
        output_root = output.resolve()
        entries: list[ChmEntry] = []
        expanded_bytes = 0
        for path in sorted(output.rglob("*"), key=lambda item: item.as_posix().casefold()):
            if path.is_symlink():
                raise _invalid_chm("CHM archive contains a symbolic link")
            if not path.is_file():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(output_root):
                raise _invalid_chm("CHM archive contains an unsafe path")
            if len(entries) >= self._max_files:
                raise _invalid_chm("CHM archive contains too many files")
            size = path.stat().st_size
            expanded_bytes += size
            if expanded_bytes > self._max_expanded_bytes:
                raise _invalid_chm("CHM archive exceeds the expanded byte limit")
            relative = _normalize_entry_path(path.relative_to(output).as_posix())
            entries.append(ChmEntry(relative, path.read_bytes()))
        return tuple(entries)


@dataclass(frozen=True, slots=True)
class _HtmlBlock:
    text: str
    heading_level: int | None = None
    anchor: str | None = None


class _TopicParser(HTMLParser):
    _BLOCK_TAGS = {
        "address",
        "blockquote",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "li",
        "p",
        "pre",
        "table",
        "td",
        "th",
        "tr",
    }
    _IGNORED_TAGS = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_HtmlBlock] = []
        self.title = ""
        self._parts: list[str] = []
        self._heading_level: int | None = None
        self._anchor: str | None = None
        self._ignored_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in self._IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if tag == "title":
            self._in_title = True
            return
        if re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self._heading_level = int(tag[1])
            self._anchor = attributes.get("id") or attributes.get("name") or None
            return
        if tag == "a" and self._heading_level is not None and self._anchor is None:
            self._anchor = attributes.get("id") or attributes.get("name") or None
        if tag == "br" or (tag in self._BLOCK_TAGS and self._parts):
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in self._IGNORED_TAGS:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if self._ignored_depth:
            return
        if tag == "title":
            self._in_title = False
            self.title = _normalize_text(" ".join(self._title_parts))
            return
        if re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self._heading_level = None
            self._anchor = None
        elif tag in self._BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
            return
        self._parts.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        text = _normalize_text("".join(self._parts))
        self._parts.clear()
        if text:
            self.blocks.append(_HtmlBlock(text, self._heading_level, self._anchor))


class _TocParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.locals: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "param":
            return
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if attributes.get("name", "").casefold() == "local" and attributes.get("value"):
            self.locals.append(attributes["value"])


class ChmParser:
    """Map one CHM Domain Document to ordered logical HTML Topic segments."""

    def __init__(self, extractor: ChmExtractor | None = None, *, max_topics: int = 4096) -> None:
        if max_topics < 1:
            raise ValueError("CHM max topics must be at least 1")
        self._extractor = extractor or ChmLibExtractor()
        self._max_topics = max_topics

    async def parse(self, source_name: str, content: bytes) -> tuple[ParsedSegment, ...]:
        del source_name
        entries = tuple(
            ChmEntry(_normalize_entry_path(entry.path), entry.content)
            for entry in await self._extractor.extract(content)
        )
        try:
            topics = self._ordered_topics(entries)
        except (UnicodeError, ValueError) as error:
            raise _invalid_chm("CHM table of contents cannot be decoded") from error
        if len(topics) > self._max_topics:
            raise _invalid_chm("CHM archive contains too many HTML topics")

        segments: list[ParsedSegment] = []
        for topic_order, entry in enumerate(topics):
            segments.extend(self._parse_topic(entry, topic_order))
        if not segments:
            raise _invalid_chm("CHM archive contains no readable HTML topics")
        return tuple(segments)

    @staticmethod
    def _ordered_topics(entries: Sequence[ChmEntry]) -> tuple[ChmEntry, ...]:
        html_by_key = {
            entry.path.casefold(): entry
            for entry in entries
            if PurePosixPath(entry.path).suffix.casefold() in _HTML_SUFFIXES
        }
        ordered: list[ChmEntry] = []
        seen: set[str] = set()
        toc_entries = sorted(
            (
                entry
                for entry in entries
                if PurePosixPath(entry.path).suffix.casefold() == _TOC_SUFFIX
            ),
            key=lambda entry: entry.path.casefold(),
        )
        for toc in toc_entries:
            parser = _TocParser()
            parser.feed(_decode_html(toc.content))
            parser.close()
            toc_parent = PurePosixPath(toc.path).parent
            for local in parser.locals:
                local_path = urlsplit(local).path.replace("\\", "/")
                decoded_path = unquote(local_path)
                if decoded_path.startswith("/"):
                    normalized = _normalize_entry_path(decoded_path, allow_root=True)
                else:
                    normalized = _normalize_entry_path((toc_parent / decoded_path).as_posix())
                key = normalized.casefold()
                if key in html_by_key and key not in seen:
                    ordered.append(html_by_key[key])
                    seen.add(key)
        for key, entry in sorted(html_by_key.items()):
            if key not in seen:
                ordered.append(entry)
        return tuple(ordered)

    @staticmethod
    def _parse_topic(entry: ChmEntry, topic_order: int) -> tuple[ParsedSegment, ...]:
        parser = _TopicParser()
        try:
            parser.feed(_decode_html(entry.content))
            parser.close()
        except (UnicodeError, ValueError) as error:
            raise _invalid_chm(f"HTML topic {entry.path} cannot be decoded") from error

        topic_title = parser.title or PurePosixPath(entry.path).stem
        heading_stack: list[tuple[int, str]] = []
        sections: list[tuple[str | None, int | None, str | None, list[str], int]] = []
        current_heading: str | None = None
        current_level: int | None = None
        current_anchor: str | None = None
        current_parts: list[str] = []
        current_start_line = 1
        next_line = 1

        def flush() -> None:
            nonlocal current_parts, current_start_line
            if current_parts:
                sections.append(
                    (
                        current_heading,
                        current_level,
                        current_anchor,
                        current_parts,
                        current_start_line,
                    )
                )
            current_parts = []
            current_start_line = next_line

        for block in parser.blocks:
            if block.heading_level is not None:
                flush()
                while heading_stack and heading_stack[-1][0] >= block.heading_level:
                    heading_stack.pop()
                heading_stack.append((block.heading_level, block.text))
                current_heading = block.text
                current_level = block.heading_level
                current_anchor = block.anchor
            current_parts.append(block.text)
            next_line += block.text.count("\n") + 2
        flush()

        result: list[ParsedSegment] = []
        active_stack: list[tuple[int, str]] = []
        for heading, level, anchor, parts, start_line in sections:
            if level is not None and heading is not None:
                while active_stack and active_stack[-1][0] >= level:
                    active_stack.pop()
                active_stack.append((level, heading))
            heading_path = " > ".join(item[1] for item in active_stack) or topic_title
            text = "\n\n".join(parts).strip()
            if not text:
                continue
            end_line = start_line + text.count("\n")
            locator_metadata = {
                "topic_path": entry.path,
                "topic_title": topic_title,
                "topic_order": str(topic_order),
                "heading_path": heading_path,
            }
            if anchor:
                locator_metadata["anchor"] = anchor
            metadata = {
                "source_type": "chm",
                "logical_document_type": "chm_topic",
                **locator_metadata,
            }
            if level is not None:
                metadata["heading_level"] = str(level)
            result.append(
                ParsedSegment(
                    text=text,
                    locator=Locator(
                        start_line=start_line,
                        end_line=end_line,
                        symbol=heading or topic_title,
                        language="html",
                        metadata=locator_metadata,
                    ),
                    metadata=metadata,
                )
            )
        return tuple(result)


def _decode_html(content: bytes) -> str:
    match = _META_CHARSET.search(content[:8192])
    declared_encoding = match.group(1).decode("ascii", "ignore") if match else ""
    candidates = [
        declared_encoding,
        "utf-8-sig",
        "cp1252",
    ]

    for encoding in candidates:
        if not encoding:
            continue
        try:
            codecs.lookup(encoding)
            return content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue

    if declared_encoding:
        try:
            codecs.lookup(declared_encoding)
            return content.decode(declared_encoding, errors="replace")
        except LookupError:
            pass

    raise UnicodeDecodeError(
        "html",
        content,
        0,
        min(1, len(content)),
        "unknown encoding",
    )


def _normalize_entry_path(path: str, *, allow_root: bool = False) -> str:
    normalized = path.replace("\\", "/")
    if "\x00" in normalized or re.match(r"^[A-Za-z]:", normalized):
        raise _invalid_chm("CHM archive contains an unsafe path")
    if normalized.startswith("/"):
        if not allow_root:
            raise _invalid_chm("CHM archive contains an unsafe path")
        normalized = normalized.lstrip("/")
    parts = PurePosixPath(normalized).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise _invalid_chm("CHM archive contains an unsafe path")
    return PurePosixPath(*parts).as_posix()


def _normalize_text(text: str) -> str:
    lines = (_WHITESPACE.sub(" ", line).strip() for line in text.splitlines() or [text])
    return "\n".join(line for line in lines if line)


def _invalid_chm(message: str) -> DomainError:
    return DomainError(DomainFailure(code="INVALID_CHM", message=message, retryable=False))
