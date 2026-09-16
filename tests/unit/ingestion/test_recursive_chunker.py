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


@pytest.mark.asyncio
async def test_recursive_chunker_coalesces_adjacent_explicit_steps_within_scope() -> None:
    """同页同章节的连续短步骤合并，避免泛化短句因语义不足而漏召回。"""

    chunker = RecursiveChunker(chunk_size=800, overlap=120)
    heading = "2. 安装与配置 > 2.1. Windows 安装"
    segments = tuple(
        ParsedSegment(
            text=f"{heading}\n\n{text}",
            locator=Locator(page_number=2, start_line=line, end_line=line),
            metadata={
                "source_type": "pdf",
                "heading_path": heading,
                "layout_type": "paragraph",
            },
        )
        for line, text in (
            (1, "第二步：选择安装路径后，并点击“安装”。"),
            (2, "第三步：等待安装完成。"),
            (3, "第四步：确认环境变量替换提示。"),
        )
    )

    drafts = await chunker.split(segments)

    assert len(drafts) == 1
    assert drafts[0].content_with_weight.count(heading) == 1
    assert "第二步：选择安装路径后" in drafts[0].content_with_weight
    assert "第三步：等待安装完成" in drafts[0].content_with_weight
    assert "第四步：确认环境变量" in drafts[0].content_with_weight
    assert drafts[0].locator.page_number == 2
    assert drafts[0].locator.start_line == 1
    assert drafts[0].locator.end_line == 3
    assert drafts[0].metadata["layout_type"] == "procedure"
    assert drafts[0].metadata["procedure_step_start"] == "2"
    assert drafts[0].metadata["procedure_step_end"] == "4"


@pytest.mark.asyncio
async def test_recursive_chunker_keeps_procedures_inside_source_boundaries() -> None:
    """步骤合并不得跨 PDF 页面、CHM Topic，CHI 索引项也不参与步骤识别。"""

    chunker = RecursiveChunker(chunk_size=800, overlap=120)
    segments = (
        ParsedSegment(
            text="第一步：启动安装程序。",
            locator=Locator(page_number=1, start_line=8, end_line=8),
            metadata={"source_type": "pdf", "heading_path": "Windows 安装"},
        ),
        ParsedSegment(
            text="第二步：选择安装路径。",
            locator=Locator(page_number=2, start_line=1, end_line=1),
            metadata={"source_type": "pdf", "heading_path": "Windows 安装"},
        ),
        ParsedSegment(
            text="第一步：创建 DomainParticipant。",
            locator=Locator(symbol="TopicA"),
            metadata={
                "source_type": "chm",
                "topic_path": "api/topic-a.html",
                "heading_path": "初始化",
            },
        ),
        ParsedSegment(
            text="第二步：创建 Publisher。",
            locator=Locator(symbol="TopicB"),
            metadata={
                "source_type": "chm",
                "topic_path": "api/topic-b.html",
                "heading_path": "初始化",
            },
        ),
        ParsedSegment(
            text="第一步：索引关键词，不是正文步骤。",
            locator=Locator(symbol="第一步"),
            metadata={"source_type": "chi", "topic_path": "api/topic-a.html"},
        ),
    )

    drafts = await chunker.split(segments)

    assert len(drafts) == 5
    assert drafts[0].locator.page_number == 1
    assert drafts[1].locator.page_number == 2
    assert drafts[2].metadata["topic_path"] == "api/topic-a.html"
    assert drafts[3].metadata["topic_path"] == "api/topic-b.html"
    assert "procedure_id" not in drafts[4].metadata


@pytest.mark.asyncio
async def test_recursive_chunker_does_not_merge_ordinary_numbered_lists() -> None:
    """普通编号列表可能是参数、枚举或目录，不得仅凭数字误判成操作流程。"""

    chunker = RecursiveChunker(chunk_size=800, overlap=120)
    segments = (
        ParsedSegment(text="1. DDS_RETCODE_OK", locator=Locator(start_line=1, end_line=1)),
        ParsedSegment(text="2. DDS_RETCODE_ERROR", locator=Locator(start_line=2, end_line=2)),
    )

    drafts = await chunker.split(segments)

    assert len(drafts) == 2
    assert all("procedure_id" not in draft.metadata for draft in drafts)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_type", "first", "second"),
    (
        ("markdown", "Step 1: Create a participant.", "Step 2: Create a publisher."),
        ("text", "步骤 1：创建参与者。", "步骤 2：创建发布者。"),
    ),
)
async def test_recursive_chunker_recognizes_explicit_steps_across_body_formats(
    source_type: str,
    first: str,
    second: str,
) -> None:
    """统一步骤策略覆盖正文格式及中英文显式标记。"""

    drafts = await RecursiveChunker(chunk_size=800, overlap=120).split(
        (
            ParsedSegment(
                text=first,
                locator=Locator(start_line=1, end_line=1),
                metadata={"source_type": source_type, "section": "quick-start"},
            ),
            ParsedSegment(
                text=second,
                locator=Locator(start_line=2, end_line=2),
                metadata={"source_type": source_type, "section": "quick-start"},
            ),
        )
    )

    assert len(drafts) == 1
    assert first in drafts[0].content_with_weight
    assert second in drafts[0].content_with_weight
    assert drafts[0].metadata["procedure_step_start"] == "1"
    assert drafts[0].metadata["procedure_step_end"] == "2"
