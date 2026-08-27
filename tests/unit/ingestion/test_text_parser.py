from __future__ import annotations

# 验证纯文本输入被解析为稳定的段落与行号定位。
import pytest

from rag_mvp.adapters.parsers.text import TextParser
from rag_mvp.domain.errors import DomainError


@pytest.mark.asyncio
async def test_text_parser_normalizes_bom_and_newlines_with_line_locator() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    parser = TextParser()

    segments = await parser.parse("guide.txt", "\ufeff第一行\r\n第二行\r第三行".encode())

    assert len(segments) == 1
    assert segments[0].text == "第一行\n第二行\n第三行"
    assert segments[0].locator.start_line == 1
    assert segments[0].locator.end_line == 3
    assert segments[0].metadata == {"source_type": "text"}


@pytest.mark.asyncio
async def test_text_parser_rejects_invalid_utf8_with_stable_error() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    with pytest.raises(DomainError) as error:
        await TextParser().parse("bad.txt", b"\xff")

    assert error.value.failure.code == "INVALID_UTF8"
    assert error.value.failure.retryable is False
