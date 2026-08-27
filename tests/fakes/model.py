"""仅测试使用的确定性 Embedding 与重排模型。"""

from __future__ import annotations

import hashlib
import math


class FakeModelGateway:
    def __init__(self, dimension: int = 8) -> None:
        """初始化测试替身的内存状态。"""
        if dimension < 1:
            raise ValueError("dimension must be at least 1")
        self.dimension = dimension
        self.embed_calls = 0
        self.rerank_calls = 0

    def _vector(self, text: str) -> tuple[float, ...]:
        """从文本派生稳定的伪向量，避免依赖真实模型。"""
        digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).digest()
        values = tuple(
            (digest[index % len(digest)] - 127.5) / 127.5 for index in range(self.dimension)
        )
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return tuple(value / norm for value in values)

    async def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        """模拟文本向量化并返回确定性向量。"""
        self.embed_calls += 1
        return [self._vector(text) for text in texts]

    async def rerank(self, query: str, passages: list[str]) -> list[float]:
        """模拟重排模型并返回可预测的分数。"""
        self.rerank_calls += 1
        query_terms = {term.casefold() for term in query.split() if term}
        return [
            len(query_terms & {term.casefold() for term in passage.split()})
            / max(len(query_terms), 1)
            for passage in passages
        ]
