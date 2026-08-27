"""针对已配置真实 Embedding 服务的连通性与语义冒烟测试。"""

from __future__ import annotations

import math

import httpx
import pytest

from rag_mvp.adapters.model.openai_compatible import OpenAICompatibleModelGateway
from rag_mvp.config import Settings


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """计算两个向量的余弦相似度，用于验证真实模型语义区分度。"""
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return numerator / (left_norm * right_norm)


def _real_gateway() -> tuple[OpenAICompatibleModelGateway, int]:
    """按 .env 构造真实 OpenAI-compatible Embedding 网关。"""
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
    """真实模型输出必须维度正确、数值有限且重复输入稳定。"""
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
    """用中文语义样本确认相关文本的相似度高于无关文本。"""
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
