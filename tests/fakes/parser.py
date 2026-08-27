"""仅供聚焦测试使用的整篇文档解析器。"""

from rag_mvp.domain.models import Locator
from rag_mvp.ports.parser import ParsedSegment


class FakeParser:
    async def parse(self, source_name: str, content: bytes) -> tuple[ParsedSegment, ...]:
        """模拟解析过程并返回可定位的文本片段。"""
        del source_name
        return (
            ParsedSegment(
                text=content.decode("utf-8"),
                locator=Locator(start_line=1, end_line=max(content.count(b"\n") + 1, 1)),
            ),
        )
