"""OpenAI-compatible Embedding adapter 的请求与降级单元测试。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from rag_mvp.adapters.model.openai_compatible import OpenAICompatibleModelGateway
from rag_mvp.domain.errors import DomainError

SECRET = "unit-test-secret"


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    """构造本测试所需的输入、替身或运行环境。"""
    return httpx.AsyncClient(
        headers={"Authorization": f"Bearer {SECRET}"},
        transport=httpx.MockTransport(handler),
    )


def _gateway(
    client: httpx.AsyncClient,
    *,
    endpoint: str = "https://model.example/v1/",
    dimension: int = 3,
    batch_size: int = 2,
    max_retries: int = 2,
) -> OpenAICompatibleModelGateway:
    """构造本测试所需的输入、替身或运行环境。"""
    return OpenAICompatibleModelGateway(
        client,
        endpoint,
        "embedding-model",
        dimension,
        batch_size,
        max_retries,
    )


@pytest.mark.asyncio
async def test_embed_normalizes_url_preserves_batch_order_and_bearer_header() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """执行测试所需的辅助操作。"""
        requests.append(request)
        assert SECRET.encode("utf-8") not in request.content
        inputs = request.read()
        payload = json.loads(inputs)
        data = [
            {"object": "embedding", "index": index, "embedding": [float(value), 1.0, 2.0]}
            for index, value in reversed(list(enumerate(payload["input"])))
        ]
        return httpx.Response(200, json={"object": "list", "data": data})

    client = _client(handler)
    gateway = _gateway(client)
    try:
        vectors = await gateway.embed(["1", "2", "3", "4", "5"])
    finally:
        await gateway.close()

    assert vectors == [
        (1.0, 1.0, 2.0),
        (2.0, 1.0, 2.0),
        (3.0, 1.0, 2.0),
        (4.0, 1.0, 2.0),
        (5.0, 1.0, 2.0),
    ]
    assert len(requests) == 3
    assert {str(request.url) for request in requests} == {"https://model.example/v1/embeddings"}
    assert all(request.headers["authorization"] == f"Bearer {SECRET}" for request in requests)
    assert SECRET not in repr(gateway)


@pytest.mark.asyncio
async def test_embed_bisects_provider_rejected_multi_input_batches() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    request_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """执行测试所需的辅助操作。"""
        payload = json.loads(request.read())
        inputs = payload["input"]
        request_sizes.append(len(inputs))
        if len(inputs) > 2:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": "invalid_parameter_error",
                        "message": "batch is too large",
                        "param": None,
                        "type": "invalid_request_error",
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {
                        "object": "embedding",
                        "index": index,
                        "embedding": [float(value), 1.0, 2.0],
                    }
                    for index, value in enumerate(inputs)
                ],
            },
        )

    client = _client(handler)
    gateway = _gateway(client, batch_size=8, max_retries=0)
    try:
        vectors = await gateway.embed(["1", "2", "3", "4", "5"])
    finally:
        await gateway.close()

    assert vectors == [
        (1.0, 1.0, 2.0),
        (2.0, 1.0, 2.0),
        (3.0, 1.0, 2.0),
        (4.0, 1.0, 2.0),
        (5.0, 1.0, 2.0),
    ]
    assert request_sizes == [5, 2, 3, 1, 2]


@pytest.mark.asyncio
async def test_embed_empty_input_does_not_call_provider() -> None:
    """验证本测试场景的预期行为与边界条件。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        """执行测试所需的辅助操作。"""
        raise AssertionError("provider must not be called for empty input")

    client = _client(handler)
    gateway = _gateway(client)
    try:
        assert await gateway.embed([]) == []
    finally:
        await gateway.close()


@pytest.mark.parametrize(
    ("response_json", "expected_code"),
    [
        ({"object": "wrong", "data": []}, "EMBEDDING_RESPONSE_INVALID"),
        ({"object": "list", "data": []}, "EMBEDDING_RESPONSE_INVALID"),
        (
            {"object": "list", "data": [{"index": 0, "embedding": [1.0, 2.0]}]},
            "EMBEDDING_DIMENSION_MISMATCH",
        ),
        (
            {"object": "list", "data": [{"index": 0, "embedding": [1.0, float("nan"), 2.0]}]},
            "EMBEDDING_RESPONSE_INVALID",
        ),
        (
            {
                "object": "list",
                "data": [
                    {"index": 0, "embedding": [1.0, 2.0, 3.0]},
                    {"index": 0, "embedding": [4.0, 5.0, 6.0]},
                ],
            },
            "EMBEDDING_RESPONSE_INVALID",
        ),
    ],
)
@pytest.mark.asyncio
async def test_embed_rejects_invalid_schema_count_dimension_and_numbers(
    response_json: dict[str, object],
    expected_code: str,
) -> None:
    """验证本测试场景的预期行为与边界条件。"""
    client = _client(
        lambda _request: httpx.Response(
            200,
            content=json.dumps(response_json).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
    )
    gateway = _gateway(client, batch_size=8)
    inputs = ["a", "b"] if len(response_json.get("data", [])) == 2 else ["a"]
    try:
        with pytest.raises(DomainError) as error:
            await gateway.embed(inputs)
    finally:
        await gateway.close()

    assert error.value.failure.code == expected_code
    assert error.value.failure.retryable is False
    assert SECRET not in str(error.value)


@pytest.mark.asyncio
async def test_auth_failure_is_non_retryable_and_redacts_provider_body() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        """执行测试所需的辅助操作。"""
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, text=f"provider leaked {SECRET}")

    client = _client(handler)
    gateway = _gateway(client, max_retries=5)
    try:
        with pytest.raises(DomainError) as error:
            await gateway.embed(["a"])
    finally:
        await gateway.close()

    assert attempts == 1
    assert error.value.failure.code == "EMBEDDING_AUTH_FAILED"
    assert error.value.failure.retryable is False
    assert SECRET not in str(error.value)


@pytest.mark.asyncio
async def test_embed_does_not_duplicate_existing_embeddings_suffix() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """执行测试所需的辅助操作。"""
        requested_urls.append(str(request.url))
        return httpx.Response(
            200,
            json={"object": "list", "data": [{"index": 0, "embedding": [1, 2, 3]}]},
        )

    client = _client(handler)
    gateway = _gateway(client, endpoint="https://model.example/v1/embeddings/")
    try:
        await gateway.embed(["a"])
    finally:
        await gateway.close()

    assert requested_urls == ["https://model.example/v1/embeddings"]


@pytest.mark.asyncio
async def test_transient_statuses_retry_with_a_bound_and_recover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证本测试场景的预期行为与边界条件。"""
    statuses = iter((429, 503, 200))
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        """执行测试所需的辅助操作。"""
        nonlocal attempts
        attempts += 1
        status = next(statuses)
        if status == 200:
            return httpx.Response(
                200,
                json={"object": "list", "data": [{"index": 0, "embedding": [1, 2, 3]}]},
            )
        return httpx.Response(status, text=f"transient {SECRET}")

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        """执行测试所需的辅助操作。"""
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    client = _client(handler)
    gateway = _gateway(client, max_retries=2)
    try:
        assert await gateway.embed(["a"]) == [(1.0, 2.0, 3.0)]
    finally:
        await gateway.close()

    assert attempts == 3
    assert sleeps == [0.1, 0.2]


@pytest.mark.asyncio
async def test_timeout_exhaustion_maps_to_retryable_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证本测试场景的预期行为与边界条件。"""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        """执行测试所需的辅助操作。"""
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("provider timed out", request=request)

    async def fake_sleep(_delay: float) -> None:
        """执行测试所需的辅助操作。"""
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    client = _client(handler)
    gateway = _gateway(client, max_retries=2)
    try:
        with pytest.raises(DomainError) as error:
            await gateway.embed(["a"])
    finally:
        await gateway.close()

    assert attempts == 3
    assert error.value.failure.code == "EMBEDDING_UNAVAILABLE"
    assert error.value.failure.retryable is True
    assert SECRET not in str(error.value)
