from __future__ import annotations

# 校验模型网关对 Embedding、Rerank 及异常的统一行为。
import httpx
import pytest

from rag_mvp.adapters.model.openai_compatible import OpenAICompatibleModelGateway
from rag_mvp.domain.errors import DomainError
from tests.fakes.model import FakeModelGateway


@pytest.mark.asyncio
async def test_fake_model_is_deterministic_and_dimensionally_stable() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    model = FakeModelGateway(dimension=8)

    first = await model.embed(["alpha", "beta", "alpha"])
    repeated = await model.embed(["alpha"])
    rerank = await model.rerank("alpha beta", ["alpha", "gamma"])

    assert len(first) == 3
    assert len(first[0]) == 8
    assert first[0] == first[2] == repeated[0]
    assert rerank[0] > rerank[1]


@pytest.mark.asyncio
async def test_unconfigured_rerank_is_explicitly_retryable_unavailable() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(500)))
    gateway = OpenAICompatibleModelGateway(
        client,
        "https://model.example/v1/embeddings",
        "embedding-model",
        3,
        8,
        0,
    )
    try:
        with pytest.raises(DomainError) as error:
            await gateway.rerank("query", ["passage"])
    finally:
        await gateway.close()

    assert error.value.failure.code == "RERANK_UNAVAILABLE"
    assert error.value.failure.retryable is True
