"""Embedding and reranking model capability boundary."""

from typing import Protocol


class ModelGateway(Protocol):
    """Provide embedding and reranking without leaking a model SDK."""

    async def embed(self, texts: list[str]) -> list[tuple[float, ...]]: ...

    async def rerank(self, query: str, passages: list[str]) -> list[float]: ...
