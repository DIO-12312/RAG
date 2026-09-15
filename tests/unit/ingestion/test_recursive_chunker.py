from __future__ import annotations

# 验证递归切块的边界、重叠、代码块完整性和定位信息。
import json
from pathlib import Path

import pytest

from rag_mvp.adapters.chunkers.recursive import RecursiveChunker
from rag_mvp.domain.models import Locator
from rag_mvp.ports.parser import ParsedSegment


@pytest.mark.asyncio
async def test_recursive_chunker_is_stable_bounded_and_overlapping() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    chunker = RecursiveChunker(chunk_size=12, overlap=4)
    segments = (
        ParsedSegment(
            text="alpha beta\ngamma delta\nepsilon",
            locator=Locator(start_line=10, end_line=12),
            metadata={"section": "intro"},
        ),
    )

    first = await chunker.split(segments)
    second = await chunker.split(segments)

    assert first == second
    assert [chunk.ordinal for chunk in first] == list(range(len(first)))
    assert all(1 <= len(chunk.content_with_weight) <= 12 for chunk in first)
    assert first[0].locator.start_line == 10
    assert first[-1].locator.end_line == 12
    assert all(chunk.metadata["section"] == "intro" for chunk in first)
    assert set(first[0].content_with_weight[-4:]) & set(first[1].content_with_weight[:4])


def test_recursive_chunker_rejects_invalid_overlap() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    with pytest.raises(ValueError, match="overlap"):
        RecursiveChunker(chunk_size=10, overlap=10)


@pytest.mark.asyncio
async def test_recursive_chunker_matches_txt_golden_fixture() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    fixture_path = Path(__file__).parents[2] / "fixtures" / "golden_chunks" / "txt.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    chunker = RecursiveChunker(fixture["chunk_size"], fixture["overlap"])

    actual = await chunker.split(
        (
            ParsedSegment(
                text=fixture["input"],
                locator=Locator(start_line=fixture["start_line"], end_line=12),
            ),
        )
    )

    assert [
        {
            "ordinal": draft.ordinal,
            "content": draft.content_with_weight,
            "start_line": draft.locator.start_line,
            "end_line": draft.locator.end_line,
        }
        for draft in actual
    ] == fixture["chunks"]


@pytest.mark.asyncio
async def test_recursive_chunker_drops_table_of_contents_segments() -> None:
    """目录页只重复章节标题与页码，不得进入索引、证据和引用。"""

    chunker = RecursiveChunker(chunk_size=800, overlap=120)
    leaders = "." * 40
    segments = (
        ParsedSegment(
            text=f"第 10 章 QoS 策略{leaders} 119",
            locator=Locator(page_number=3, start_line=14, end_line=16),
            metadata={"source_type": "pdf"},
        ),
        ParsedSegment(
            text=f"| 1.1 | 分布式系统{leaders} 1 |\n| 1.2 | 中间件{leaders} 2 |",
            locator=Locator(page_number=3, start_line=17, end_line=18),
            metadata={"source_type": "pdf"},
        ),
        ParsedSegment(
            text="Durability QoS 控制 DataReader 是否获取 DataWriter 发送的历史数据。",
            locator=Locator(page_number=127, start_line=30, end_line=33),
            metadata={"source_type": "pdf"},
        ),
    )

    drafts = await chunker.split(segments)

    assert len(drafts) == 1
    assert drafts[0].ordinal == 0
    assert "Durability QoS" in drafts[0].content_with_weight


@pytest.mark.asyncio
async def test_recursive_chunker_keeps_ellipsis_and_short_page_numbers() -> None:
    """正文里的省略号与带页码的表格行不能被误判成目录条目。"""

    chunker = RecursiveChunker(chunk_size=800, overlap=120)
    segments = (
        ParsedSegment(
            text="更多...\n\nDCPSDLL void DDS_DomainParticipantFactory_get_qos(...)",
            locator=Locator(start_line=1, end_line=2),
            metadata={"source_type": "chm"},
        ),
        ParsedSegment(
            text="内存：256M\n磁盘空间：开发机 500M，运行机取决于应用大小",
            locator=Locator(page_number=1, start_line=4, end_line=5),
            metadata={"source_type": "pdf"},
        ),
    )

    drafts = await chunker.split(segments)

    assert len(drafts) == 2
    assert "更多..." in drafts[0].content_with_weight
    assert "内存：256M" in drafts[1].content_with_weight
