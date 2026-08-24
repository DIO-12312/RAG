"""Smoke and semantic checks against the configured real embedding provider."""

from __future__ import annotations

import math

import httpx
import pytest

from rag_mvp.adapters.model.openai_compatible import OpenAICompatibleModelGateway
from rag_mvp.config import Settings


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return numerator / (left_norm * right_norm)


def _real_gateway() -> tuple[OpenAICompatibleModelGateway, int]:
    profile = Settings().require_embedding_profile()
    client = httpx.AsyncClient(
        headers={"Authorization": f"Bearer {profile.api_key.get_secret_value()}"},
        timeout=profile.timeout_seconds,
    )
    return (
        OpenAICompatibleModelGateway(
            client,
            profile.endpoint,
            profile.model,
            profile.dimension,
            min(profile.batch_size, 2),
            profile.max_retries,
        ),
        profile.dimension,
    )


@pytest.mark.model_integration
@pytest.mark.asyncio
async def test_real_embedding_returns_finite_declared_dimension_and_stable_duplicates() -> None:
    gateway, dimension = _real_gateway()
    try:
        vectors = await gateway.embed(["RAG 检索测试", "RAG 检索测试", "数据库事务"])
    finally:
        await gateway.close()

    assert len(vectors) == 3
    assert all(len(vector) == dimension for vector in vectors)
    assert all(math.isfinite(value) for vector in vectors for value in vector)
    assert _cosine(vectors[0], vectors[1]) > 0.999


@pytest.mark.model_integration
@pytest.mark.asyncio
async def test_real_embedding_ranks_related_chinese_text_above_unrelated_text() -> None:
    gateway, _dimension = _real_gateway()
    try:
        query, related, unrelated = await gateway.embed(
            [
                "如何使用向量检索找到相关文档？",
                "向量数据库通过语义相似度召回知识库内容。",
                "今天晚餐准备烤面包和水果沙拉。",
            ]
        )
    finally:
        await gateway.close()

    assert _cosine(query, related) > _cosine(query, unrelated)
