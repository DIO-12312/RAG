"""Microsoft HTML Help ``.chi`` keyword-to-Topic index parser."""

from __future__ import annotations

import struct
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from rag_mvp.adapters.parsers.chm import ChmExtractor
from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.domain.models import Locator
from rag_mvp.ports.parser import ParsedSegment

_BTREE_STREAM = "$WWKeywordLinks/BTree"
_TOPICS_STREAM = "#TOPICS"
_URL_TABLE_STREAM = "#URLTBL"
_URL_STRINGS_STREAM = "#URLSTR"
_TITLE_STRINGS_STREAM = "#STRINGS"
_BTREE_HEADER_SIZE = 76
_BTREE_LEAF_HEADER_SIZE = 12
_TOPIC_RECORD_SIZE = 16
_URL_TABLE_RECORD_SIZE = 12
_MAX_BTREE_BLOCK_SIZE = 64 * 1024
_MAX_TOPIC_REFERENCES_PER_KEYWORD = 4096


@dataclass(frozen=True, slots=True)
class _KeywordRecord:
    ordinal: int
    value: str
    topic_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _TopicReference:
    index: int
    title: str
    url: str
    path: str
    anchor: str | None


class ChiParser:
    """Convert binary CHI keyword links into Topic-addressable segments."""

    def __init__(
        self,
        extractor: ChmExtractor,
        *,
        keywords_per_segment: int = 32,
        max_keywords: int = 16384,
    ) -> None:
        if keywords_per_segment < 1:
            raise ValueError("CHI keywords per segment must be at least 1")
        if max_keywords < keywords_per_segment:
            raise ValueError("CHI max keywords must cover one segment")
        self._extractor = extractor
        self._keywords_per_segment = keywords_per_segment
        self._max_keywords = max_keywords

    async def parse(self, source_name: str, content: bytes) -> tuple[ParsedSegment, ...]:
        try:
            entries = await self._extractor.extract(content)
        except DomainError as error:
            if error.failure.code == "CHM_EXTRACTOR_UNAVAILABLE":
                raise
            raise _invalid_chi("source is not a readable CHI archive") from error

        try:
            streams = {_stream_key(entry.path): entry.content for entry in entries}
            records = _keyword_records(
                _required_stream(streams, _BTREE_STREAM),
                self._max_keywords,
            )
            references = _topic_references(
                records,
                topics=_required_stream(streams, _TOPICS_STREAM),
                url_table=_required_stream(streams, _URL_TABLE_STREAM),
                url_strings=_required_stream(streams, _URL_STRINGS_STREAM),
                title_strings=_required_stream(streams, _TITLE_STRINGS_STREAM),
            )
            segments = self._segments(source_name, records, references)
        except (KeyError, UnicodeError, ValueError, struct.error) as error:
            raise _invalid_chi("CHI keyword-to-Topic index is malformed") from error

        if not segments:
            raise _invalid_chi("CHI keyword index contains no readable Topic links")
        return segments

    def _segments(
        self,
        source_name: str,
        records: Sequence[_KeywordRecord],
        references: dict[int, _TopicReference],
    ) -> tuple[ParsedSegment, ...]:
        by_topic: dict[int, list[_KeywordRecord]] = defaultdict(list)
        seen: set[tuple[int, str]] = set()
        for record in records:
            for topic_index in dict.fromkeys(record.topic_indices):
                if topic_index not in references:
                    continue
                key = (topic_index, record.value.casefold())
                if key in seen:
                    continue
                seen.add(key)
                by_topic[topic_index].append(record)

        associated_name = f"{PurePosixPath(source_name).stem}.chm"
        segments: list[ParsedSegment] = []
        for topic_index, topic_records in by_topic.items():
            reference = references[topic_index]
            for group_start in range(0, len(topic_records), self._keywords_per_segment):
                group = topic_records[group_start : group_start + self._keywords_per_segment]
                keyword_start = min(record.ordinal for record in group)
                keyword_end = max(record.ordinal for record in group)
                metadata = {
                    "source_type": "chi",
                    "logical_document_type": "chm_index",
                    "chi_stream": _BTREE_STREAM,
                    "associated_chm_source_name": associated_name,
                    "chi_topic_index": str(topic_index),
                    "topic_path": reference.path,
                    "topic_title": reference.title,
                    "topic_url": reference.url,
                    "keyword_start": str(keyword_start),
                    "keyword_end": str(keyword_end),
                }
                if reference.anchor:
                    metadata["anchor"] = reference.anchor
                locator_metadata = {
                    key: value
                    for key, value in metadata.items()
                    if key not in {"source_type", "logical_document_type"}
                }
                segments.append(
                    ParsedSegment(
                        text="\n".join(record.value for record in group),
                        locator=Locator(
                            start_line=keyword_start,
                            end_line=keyword_end,
                            symbol=_leaf_keyword(group[0].value),
                            language="chi",
                            metadata=locator_metadata,
                        ),
                        metadata=metadata,
                    )
                )
        return tuple(segments)


def _stream_key(path: str) -> str:
    return path.replace("\\", "/").strip("/").casefold()


def _required_stream(streams: dict[str, bytes], name: str) -> bytes:
    try:
        return streams[name.casefold()]
    except KeyError as error:
        raise KeyError(f"missing CHI stream {name}") from error


def _keyword_records(content: bytes, limit: int) -> tuple[_KeywordRecord, ...]:
    """Decode the linked listing blocks in ``$WWKeywordLinks/BTree``."""

    if len(content) < _BTREE_HEADER_SIZE + _BTREE_LEAF_HEADER_SIZE:
        raise ValueError("keyword BTree is truncated")
    block_size = struct.unpack_from("<H", content, 4)[0]
    if not _BTREE_LEAF_HEADER_SIZE < block_size <= _MAX_BTREE_BLOCK_SIZE:
        raise ValueError("keyword BTree block size is invalid")
    payload_size = len(content) - _BTREE_HEADER_SIZE
    if payload_size % block_size:
        raise ValueError("keyword BTree has a partial block")
    block_count = payload_size // block_size

    records: list[_KeywordRecord] = []
    visited: set[int] = set()
    block_index = 0
    while block_index != 0xFFFFFFFF and len(records) < limit:
        if block_index >= block_count or block_index in visited:
            raise ValueError("keyword BTree listing chain is invalid")
        visited.add(block_index)
        block_start = _BTREE_HEADER_SIZE + block_index * block_size
        block_end = block_start + block_size
        free_space, entry_count, _previous, next_block = struct.unpack_from(
            "<HHII", content, block_start
        )
        if free_space > block_size - _BTREE_LEAF_HEADER_SIZE:
            raise ValueError("keyword BTree free-space marker is invalid")
        data_end = block_end - free_space
        cursor = block_start + _BTREE_LEAF_HEADER_SIZE

        for _ in range(entry_count):
            if len(records) >= limit:
                break
            keyword, cursor = _read_utf16z(content, cursor, data_end)
            if cursor + 16 > data_end:
                raise ValueError("keyword BTree entry header is truncated")
            see_also, _depth, _char_index, _unknown, pair_count = struct.unpack_from(
                "<HHIII", content, cursor
            )
            cursor += 16
            if pair_count > _MAX_TOPIC_REFERENCES_PER_KEYWORD:
                raise ValueError("keyword BTree Topic fan-out is too large")
            if see_also == 2:
                _see_also, cursor = _read_utf16z(content, cursor, data_end)
                topic_indices: tuple[int, ...] = ()
            elif see_also == 0:
                pair_bytes = pair_count * 4
                if cursor + pair_bytes > data_end:
                    raise ValueError("keyword BTree Topic references are truncated")
                topic_indices = tuple(struct.unpack_from(f"<{pair_count}I", content, cursor))
                cursor += pair_bytes
            else:
                raise ValueError("keyword BTree link type is invalid")
            if cursor + 8 > data_end:
                raise ValueError("keyword BTree entry trailer is truncated")
            cursor += 8

            normalized = " ".join(keyword.split()).strip()
            if normalized and len(normalized) <= 488 and topic_indices:
                records.append(
                    _KeywordRecord(
                        ordinal=len(records) + 1,
                        value=normalized,
                        topic_indices=topic_indices,
                    )
                )
        block_index = next_block
    return tuple(records)


def _topic_references(
    records: Sequence[_KeywordRecord],
    *,
    topics: bytes,
    url_table: bytes,
    url_strings: bytes,
    title_strings: bytes,
) -> dict[int, _TopicReference]:
    references: dict[int, _TopicReference] = {}
    for topic_index in dict.fromkeys(
        topic_index for record in records for topic_index in record.topic_indices
    ):
        topic_offset = topic_index * _TOPIC_RECORD_SIZE
        if topic_offset + _TOPIC_RECORD_SIZE > len(topics):
            raise ValueError("CHI Topic index is out of range")
        _toc_offset, title_offset, url_table_offset, _flags, _unknown = struct.unpack_from(
            "<IIIHH", topics, topic_offset
        )
        if url_table_offset + _URL_TABLE_RECORD_SIZE > len(url_table):
            raise ValueError("CHI URL table offset is out of range")
        _unique_id, mapped_topic_index, url_string_offset = struct.unpack_from(
            "<III", url_table, url_table_offset
        )
        if mapped_topic_index != topic_index:
            raise ValueError("CHI URL table points at a different Topic")

        # A #URLSTR record has two DWORD fields before its null-terminated URL.
        url = _read_c_string(url_strings, url_string_offset + 8)
        normalized_url = url.replace("\\", "/").lstrip("/").strip()
        path, separator, anchor = normalized_url.partition("#")
        if not _is_safe_topic_path(path):
            continue
        title = _read_c_string(title_strings, title_offset) if title_offset != 0xFFFFFFFF else ""
        title = " ".join(title.split()).strip() or PurePosixPath(path).stem
        references[topic_index] = _TopicReference(
            index=topic_index,
            title=title,
            url=normalized_url,
            path=path,
            anchor=anchor if separator and anchor else None,
        )
    return references


def _read_utf16z(content: bytes, offset: int, end: int) -> tuple[str, int]:
    if offset < 0 or end > len(content) or offset >= end:
        raise ValueError("UTF-16 string offset is invalid")
    cursor = offset
    while cursor + 1 < end and content[cursor : cursor + 2] != b"\x00\x00":
        cursor += 2
    if cursor + 1 >= end:
        raise ValueError("UTF-16 string is unterminated")
    return content[offset:cursor].decode("utf-16le"), cursor + 2


def _read_c_string(content: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(content):
        raise ValueError("string offset is invalid")
    end = content.find(b"\x00", offset)
    if end < 0:
        raise ValueError("string is unterminated")
    raw = content[offset:end]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def _is_safe_topic_path(path: str) -> bool:
    candidate = PurePosixPath(path)
    return (
        bool(path)
        and not candidate.is_absolute()
        and ".." not in candidate.parts
        and ":" not in path
    )


def _leaf_keyword(value: str) -> str:
    return value.rsplit(",", 1)[-1].strip()


def _invalid_chi(message: str) -> DomainError:
    return DomainError(DomainFailure("INVALID_CHI", message, retryable=False))
