from __future__ import annotations

# 校验解析器和切块器输出可被摄取流水线稳定消费。
import pytest

from tests.fakes.chunker import FakeChunker
from tests.fakes.parser import FakeParser


@pytest.mark.asyncio
async def test_parser_and_chunker_preserve_text_order_and_locator() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    parser = FakeParser()
    chunker = FakeChunker()

    segments = await parser.parse("guide.txt", "第一行\n第二行".encode())
    chunks = await chunker.split(segments)

    assert [chunk.ordinal for chunk in chunks] == [0]
    assert chunks[0].content_with_weight == "第一行\n第二行"
    assert chunks[0].locator.start_line == 1
    assert chunks[0].locator.end_line == 2
