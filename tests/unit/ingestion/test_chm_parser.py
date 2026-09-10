from __future__ import annotations

import struct
from collections.abc import Sequence

import pytest

from rag_mvp.adapters.chunkers.recursive import RecursiveChunker
from rag_mvp.adapters.parsers.chi import ChiParser
from rag_mvp.adapters.parsers.chm import ChmEntry, ChmLibExtractor, ChmParser
from rag_mvp.adapters.parsers.router import SourceParserRouter
from rag_mvp.domain.errors import DomainError, DomainFailure


class _FakeChmExtractor:
    def __init__(self, entries: Sequence[ChmEntry]) -> None:
        self._entries = tuple(entries)

    async def extract(self, content: bytes) -> tuple[ChmEntry, ...]:
        assert content == b"fake-chm"
        return self._entries


class _FakeChiExtractor:
    async def extract(self, content: bytes) -> tuple[ChmEntry, ...]:
        assert content == b"fake-chi"
        return _chi_entries(
            (
                ("CCondition, DDS_WaitSet_wait", 0),
                ("CCondition, 条件等待", 0),
                ("CDomain, DDS_DomainParticipantFactory_create_participant", 1),
            ),
            (
                ("DDS_WaitSet_wait", "group___c_infrastruct.html#ga-wait"),
                (
                    "DDS_DomainParticipantFactory_create_participant",
                    "group___c_domain.html#ga-create",
                ),
            ),
        )


class _BrokenChiExtractor:
    async def extract(self, content: bytes) -> tuple[ChmEntry, ...]:
        del content
        raise DomainError(DomainFailure("INVALID_CHM", "broken archive", retryable=False))


def _chi_entries(
    keyword_topics: Sequence[tuple[str, int]],
    topics: Sequence[tuple[str, str]],
) -> tuple[ChmEntry, ...]:
    block_size = 2048
    header = bytearray(76)
    struct.pack_into("<H", header, 4, block_size)
    block = bytearray(block_size)
    cursor = 12
    for entry_index, (keyword, topic_index) in enumerate(keyword_topics):
        encoded = keyword.encode("utf-16le") + b"\x00\x00"
        block[cursor : cursor + len(encoded)] = encoded
        cursor += len(encoded)
        struct.pack_into("<HHIII", block, cursor, 0, 0, 0, 0, 1)
        cursor += 16
        struct.pack_into("<I", block, cursor, topic_index)
        cursor += 4
        struct.pack_into("<II", block, cursor, 1, entry_index * 13)
        cursor += 8
    struct.pack_into(
        "<HHII", block, 0, block_size - cursor, len(keyword_topics), 0xFFFFFFFF, 0xFFFFFFFF
    )

    title_strings = bytearray(b"\x00")
    url_strings = bytearray()
    topic_records = bytearray()
    url_table = bytearray()
    for topic_index, (title, url) in enumerate(topics):
        title_offset = len(title_strings)
        title_strings.extend(title.encode() + b"\x00")
        url_offset = len(url_strings)
        url_strings.extend(b"\x00" * 8 + url.encode() + b"\x00")
        url_table_offset = len(url_table)
        url_table.extend(struct.pack("<III", topic_index + 100, topic_index, url_offset))
        topic_records.extend(struct.pack("<IIIHH", 0, title_offset, url_table_offset, 6, 0))
    return (
        ChmEntry("$WWKeywordLinks/BTree", bytes(header + block)),
        ChmEntry("#TOPICS", bytes(topic_records)),
        ChmEntry("#URLTBL", bytes(url_table)),
        ChmEntry("#URLSTR", bytes(url_strings)),
        ChmEntry("#STRINGS", bytes(title_strings)),
    )


def _archive_entries() -> tuple[ChmEntry, ...]:
    return (
        ChmEntry(
            "book.hhc",
            b'<object><param name="Local" value="chapter-2.html">'
            b'<param name="Local" value="chapter-1.html#intro"></object>',
        ),
        ChmEntry(
            "chapter-1.html",
            b'<html><head><meta charset="utf-8"><title>First Topic</title>'
            b"<style>hidden style</style></head><body>"
            b'<h1 id="intro">Overview</h1><p>Alpha paragraph.</p>'
            b"<h2>Details</h2><p>Beta sentence. Gamma sentence.</p>"
            b"<script>hidden script</script></body></html>",
        ),
        ChmEntry(
            "chapter-2.html",
            b"<html><title>Second Topic</title><body><p>Delta evidence.</p></body></html>",
        ),
    )


@pytest.mark.asyncio
async def test_chm_parser_orders_topics_and_preserves_heading_provenance() -> None:
    parser = ChmParser(_FakeChmExtractor(_archive_entries()))

    segments = await parser.parse("manual.chm", b"fake-chm")

    assert [segment.metadata["topic_path"] for segment in segments] == [
        "chapter-2.html",
        "chapter-1.html",
        "chapter-1.html",
    ]
    assert [segment.metadata["heading_path"] for segment in segments] == [
        "Second Topic",
        "Overview",
        "Overview > Details",
    ]
    assert segments[1].locator.symbol == "Overview"
    assert segments[1].locator.language == "html"
    assert segments[1].locator.metadata["anchor"] == "intro"
    assert segments[2].locator.start_line > segments[1].locator.end_line
    assert segments[1].metadata["logical_document_type"] == "chm_topic"
    assert all(segment.metadata["section_id"].startswith("section-") for segment in segments)
    assert segments[0].metadata["parent_section_id"] == ""
    assert segments[1].metadata["parent_section_id"] == ""
    assert segments[2].metadata["parent_section_id"] == segments[1].metadata["section_id"]
    assert [segment.metadata["heading_level"] for segment in segments] == ["0", "1", "2"]
    assert segments == await parser.parse("manual.chm", b"fake-chm")
    assert "hidden script" not in " ".join(segment.text for segment in segments)
    assert "hidden style" not in " ".join(segment.text for segment in segments)


@pytest.mark.asyncio
async def test_chm_parser_prefers_main_content_and_removes_navigation_noise() -> None:
    html = b"""<html><head><title>DDS WaitSet</title></head><body>
    <div id="main-nav"><a>Home</a><a>Related Pages</a><a>Classes</a></div>
    <div class="navpath">Package / DDS / WaitSet</div>
    <main><h1 id="wait">DDS_WaitSet_wait</h1>
    <p>Blocks until a condition is triggered or the timeout expires.</p></main>
    <footer>Generated by documentation tooling</footer>
    </body></html>"""
    parser = ChmParser(_FakeChmExtractor((ChmEntry("waitset.html", html),)))

    segments = await parser.parse("manual.chm", b"fake-chm")

    text = "\n".join(segment.text for segment in segments)
    assert "DDS_WaitSet_wait" in text
    assert "Blocks until a condition" in text
    assert "Home" not in text
    assert "Related Pages" not in text
    assert "Generated by" not in text


@pytest.mark.asyncio
async def test_chm_topic_and_heading_segments_are_hard_chunk_boundaries() -> None:
    parser = ChmParser(_FakeChmExtractor(_archive_entries()))
    segments = await parser.parse("manual.chm", b"fake-chm")

    chunks = await RecursiveChunker(chunk_size=24, overlap=4).split(segments)
    children = [chunk for chunk in chunks if chunk.metadata["chunk_role"] == "section_child"]
    parents = [chunk for chunk in chunks if chunk.metadata["chunk_role"] == "section_parent"]

    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    assert all(len(chunk.content_with_weight.rsplit("\n\n", 1)[-1]) <= 24 for chunk in children)
    assert len(parents) == len(segments)
    assert all("Section overview:" in chunk.content_with_weight for chunk in parents)
    assert all(
        len(chunk.content_with_weight.rsplit("Section overview:\n", 1)[-1]) <= 48
        for chunk in parents
    )
    assert all(chunk.metadata["source_type"] == "chm" for chunk in chunks)
    for section_id in {chunk.metadata["section_id"] for chunk in children}:
        section_chunks = [chunk for chunk in children if chunk.metadata["section_id"] == section_id]
        assert [chunk.metadata["chunk_index_in_section"] for chunk in section_chunks] == [
            str(index) for index in range(len(section_chunks))
        ]
        assert all(
            chunk.metadata["section_chunk_count"] == str(len(section_chunks))
            for chunk in section_chunks
        )
    assert all(chunk.content_with_weight.startswith("Topic: ") for chunk in chunks)
    assert all("\nHeading: " in chunk.content_with_weight for chunk in chunks)
    assert all("\nSymbol: " in chunk.content_with_weight for chunk in chunks)
    assert not any(
        "Delta" in chunk.content_with_weight and "Alpha" in chunk.content_with_weight
        for chunk in chunks
    )
    assert not any(
        "Alpha" in chunk.content_with_weight and "Beta" in chunk.content_with_weight
        for chunk in chunks
    )


@pytest.mark.asyncio
async def test_chm_parser_decodes_declared_legacy_charset() -> None:
    html = (
        '<html><head><meta charset="gb2312"><title>中文主题</title></head>'
        "<body><h1>安装</h1><p>配置知识库。</p></body></html>"
    ).encode("gb2312")
    parser = ChmParser(_FakeChmExtractor((ChmEntry("guide.htm", html),)))

    segments = await parser.parse("guide.chm", b"fake-chm")

    assert segments[0].metadata["topic_title"] == "中文主题"
    assert segments[0].locator.symbol == "安装"
    assert "配置知识库" in segments[0].text


@pytest.mark.asyncio
async def test_chm_parser_recovers_isolated_invalid_declared_charset_bytes() -> None:
    html = (
        b'<html><head><meta charset="utf-8"><title>Damaged Topic</title></head>'
        b"<body><h1>Recovery</h1><p>before \x81 after</p></body></html>"
    )
    parser = ChmParser(_FakeChmExtractor((ChmEntry("damaged.html", html),)))

    segments = await parser.parse("manual.chm", b"fake-chm")

    assert segments[0].metadata["topic_title"] == "Damaged Topic"
    assert segments[0].locator.symbol == "Recovery"
    assert "before \ufffd after" in segments[0].text


@pytest.mark.asyncio
async def test_router_selects_injected_chm_parser() -> None:
    parser = ChmParser(_FakeChmExtractor(_archive_entries()))

    segments = await SourceParserRouter(chm_parser=parser).parse("MANUAL.CHM", b"fake-chm")

    assert segments[0].metadata["source_type"] == "chm"


@pytest.mark.asyncio
async def test_chi_parser_extracts_keyword_records_with_sidecar_provenance() -> None:
    segments = await ChiParser(_FakeChiExtractor(), keywords_per_segment=2).parse(
        "ZRDDS_C_UserManual.chi", b"fake-chi"
    )

    assert len(segments) == 2
    assert "DDS_WaitSet_wait" in segments[0].text
    assert "条件等待" in segments[0].text
    assert segments[0].metadata["topic_path"] == "group___c_infrastruct.html"
    assert segments[0].metadata["topic_url"] == "group___c_infrastruct.html#ga-wait"
    assert segments[0].metadata["topic_title"] == "DDS_WaitSet_wait"
    assert segments[0].metadata["anchor"] == "ga-wait"
    assert segments[0].metadata["chi_topic_index"] == "0"
    assert segments[0].metadata["source_type"] == "chi"
    assert segments[0].metadata["logical_document_type"] == "chm_index"
    assert segments[0].metadata["associated_chm_source_name"] == "ZRDDS_C_UserManual.chm"
    assert segments[0].locator.language == "chi"
    assert segments[0].locator.metadata["chi_stream"] == "$WWKeywordLinks/BTree"
    assert segments[0].locator.symbol == "DDS_WaitSet_wait"


@pytest.mark.asyncio
async def test_chi_chunks_weight_keyword_topic_url_and_associated_chm() -> None:
    segments = await ChiParser(_FakeChiExtractor(), keywords_per_segment=2).parse(
        "ZRDDS_C_UserManual.chi", b"fake-chi"
    )

    chunks = await RecursiveChunker(chunk_size=800, overlap=120).split(segments[:1])

    assert chunks[0].content_with_weight.startswith("Index keyword: DDS_WaitSet_wait")
    assert "\nTopic: DDS_WaitSet_wait" in chunks[0].content_with_weight
    assert "\nTopic path: group___c_infrastruct.html" in chunks[0].content_with_weight
    assert "\nTopic URL: group___c_infrastruct.html#ga-wait" in chunks[0].content_with_weight
    assert "\nAssociated CHM: ZRDDS_C_UserManual.chm" in chunks[0].content_with_weight


@pytest.mark.asyncio
async def test_router_selects_chi_parser() -> None:
    parser = ChiParser(_FakeChiExtractor())
    segments = await SourceParserRouter(chi_parser=parser).parse("manual.chi", b"fake-chi")

    assert segments[0].metadata["source_type"] == "chi"


@pytest.mark.asyncio
async def test_chi_parser_maps_malformed_archive_to_invalid_chi() -> None:
    with pytest.raises(DomainError) as error:
        await ChiParser(_BrokenChiExtractor()).parse("broken.chi", b"broken")

    assert error.value.failure.code == "INVALID_CHI"


@pytest.mark.asyncio
async def test_chi_parser_rejects_keyword_topic_index_outside_topics_stream() -> None:
    class _OutOfRangeExtractor:
        async def extract(self, content: bytes) -> tuple[ChmEntry, ...]:
            del content
            return _chi_entries((("DDS_Invalid_topic", 9),), (("Only topic", "only.html"),))

    with pytest.raises(DomainError) as error:
        await ChiParser(_OutOfRangeExtractor()).parse("broken.chi", b"fake-chi")

    assert error.value.failure.code == "INVALID_CHI"


@pytest.mark.asyncio
async def test_chm_parser_rejects_unsafe_topic_path() -> None:
    parser = ChmParser(_FakeChmExtractor((ChmEntry("../escape.html", b"<p>bad</p>"),)))

    with pytest.raises(DomainError) as error:
        await parser.parse("bad.chm", b"fake-chm")

    assert error.value.failure.code == "INVALID_CHM"


@pytest.mark.asyncio
async def test_chm_parser_rejects_absolute_topic_path() -> None:
    parser = ChmParser(_FakeChmExtractor((ChmEntry("/escape.html", b"<p>bad</p>"),)))

    with pytest.raises(DomainError) as error:
        await parser.parse("bad.chm", b"fake-chm")

    assert error.value.failure.code == "INVALID_CHM"


@pytest.mark.asyncio
async def test_chmlib_extractor_rejects_non_chm_before_starting_process() -> None:
    extractor = ChmLibExtractor(executable="definitely-not-installed")

    with pytest.raises(DomainError) as error:
        await extractor.extract(b"not-a-chm")

    assert error.value.failure.code == "INVALID_CHM"
