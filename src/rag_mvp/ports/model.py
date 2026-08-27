"""Embedding 与 Rerank 模型能力边界，禁止模型 SDK 泄漏到应用层。"""

from typing import Protocol


class ModelGateway(Protocol):
    """Provide embedding and reranking without leaking a model SDK."""

    # 实现 embed 对应的局部职责。
    async def embed(self, texts: list[str]) -> list[tuple[float, ...]]: ...

    # 实现 rerank 对应的局部职责。
    async def rerank(self, query: str, passages: list[str]) -> list[float]: ...
