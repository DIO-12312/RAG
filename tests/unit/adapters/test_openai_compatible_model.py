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
    max_concurrency: int = 2,
    jitter: Callable[[], float] | None = None,
    clock: FakeClock | None = None,
) -> OpenAICompatibleModelGateway:
    """构造本测试所需的输入、替身或运行环境。"""
    return OpenAICompatibleModelGateway(
        client,
        endpoint,
        "embedding-model",
        dimension,
        batch_size,
        max_retries,
        max_concurrency,
        jitter=jitter,
        sleep=clock.sleep if clock else None,
        monotonic=clock.monotonic if clock else None,
    )


class FakeClock:
    """以确定性时间替代真实等待，让退避断言不依赖 wall clock。"""

    def __init__(self) -> None:
        """初始化测试时钟状态。"""
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        """返回当前测试时间。"""
        return self.now

    async def sleep(self, delay: float) -> None:
        """记录并推进测试时间。"""
        self.sleeps.append(delay)
        self.now += delay


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


@pytest.mark.asyncio
async def test_embed_limits_batch_concurrency_and_preserves_order() -> None:
    """并发批次不得超过配置上限，返回顺序仍与输入完全一致。"""

    active = 0
    maximum_active = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, maximum_active
        payload = json.loads(request.read())
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"index": index, "embedding": [float(value), 1.0, 2.0]}
                    for index, value in enumerate(payload["input"])
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = _gateway(client, batch_size=1, max_concurrency=2)
    try:
        vectors = await gateway.embed(["1", "2", "3", "4", "5"])
    finally:
        await gateway.close()

    assert maximum_active == 2
    assert [vector[0] for vector in vectors] == [1.0, 2.0, 3.0, 4.0, 5.0]


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
async def test_transient_statuses_retry_with_a_bound_and_recover() -> None:
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

    clock = FakeClock()
    client = _client(handler)
    gateway = _gateway(client, max_retries=2, jitter=lambda: 1.0, clock=clock)
    try:
        assert await gateway.embed(["a"]) == [(1.0, 2.0, 3.0)]
    finally:
        await gateway.close()

    assert attempts == 3
    # 429 让整批暂停 1s，随后的指数退避依次为 1s、2s。
    assert clock.sleeps == [1.0, 2.0]


@pytest.mark.asyncio
async def test_throttling_honours_retry_after_and_pauses_every_batch() -> None:
    """429 必须按 Retry-After 退避，并让同文档的其他批次一起放慢。"""

    statuses = iter((429, 429, 200, 200))
    seen: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        """执行测试所需的辅助操作。"""
        seen.append(1.0)
        status = next(statuses)
        if status == 200:
            payload = {
                "object": "list",
                "data": [
                    {"index": 0, "embedding": [1, 2, 3]},
                    {"index": 1, "embedding": [4, 5, 6]},
                ],
            }
            return httpx.Response(200, json=payload)
        return httpx.Response(
            429,
            headers={"Retry-After": "7"},
            json={"error": {"code": "Throttling.RateQuota", "message": "rate limit exceeded"}},
        )

    clock = FakeClock()
    client = _client(handler)
    gateway = _gateway(client, max_retries=3, jitter=lambda: 1.0, clock=clock)
    try:
        vectors = await gateway.embed(["a", "b"])
    finally:
        await gateway.close()

    assert len(vectors) == 2
    assert clock.sleeps
    assert all(delay == 7.0 for delay in clock.sleeps)


@pytest.mark.asyncio
async def test_exhausted_throttling_reports_provider_status_and_code() -> None:
    """限流耗尽后的失败信息必须包含提供方状态码与错误码，且不得泄露凭据或输入。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        """执行测试所需的辅助操作。"""
        return httpx.Response(
            429,
            headers={"Retry-After": "2"},
            json={
                "error": {
                    "code": "Throttling.RateQuota",
                    "message": f"requests rate limit exceeded {SECRET}",
                }
            },
        )

    clock = FakeClock()
    client = _client(handler)
    gateway = _gateway(client, max_retries=1, jitter=lambda: 1.0, clock=clock)
    try:
        with pytest.raises(DomainError) as error:
            await gateway.embed(["a"])
    finally:
        await gateway.close()

    failure = error.value.failure
    assert failure.code == "EMBEDDING_UNAVAILABLE"
    assert failure.retryable is True
    assert "status=429" in failure.message
    assert "Throttling.RateQuota" in failure.message
    assert SECRET not in str(error.value)


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


@pytest.mark.asyncio
async def test_batch_limit_from_provider_is_learned_and_reused() -> None:
    """提供方声明单请求上限后，后续批次不再逐个撞 400 再二分。"""

    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """执行测试所需的辅助操作。"""
        batch = json.loads(request.read())["input"]
        sizes.append(len(batch))
        if len(batch) > 3:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": "InvalidParameter",
                        "message": "batch size is invalid, it should not be larger than 3.",
                    }
                },
            )
        payload = {
            "object": "list",
            "data": [{"index": index, "embedding": [1, 2, 3]} for index in range(len(batch))],
        }
        return httpx.Response(200, json=payload)

    client = _client(handler)
    gateway = _gateway(client, batch_size=8, max_retries=0, jitter=lambda: 1.0)
    try:
        vectors = await gateway.embed([f"t{index}" for index in range(8)])
    finally:
        await gateway.close()

    assert len(vectors) == 8
    # 第一次 8 条被拒后按 4/4 二分，两侧的 4 条再次被拒再各分 2/2，
    # 之后学习到上限 3，剩余调用不再出现超过 3 条的请求。
    assert sizes[0] == 8
    assert len([size for size in sizes if size > 3]) == 3
    assert all(size <= 4 for size in sizes[1:])


@pytest.mark.asyncio
async def test_quota_exhaustion_is_reported_as_quota_not_transport() -> None:
    """额度用尽必须返回可区分的错误码，避免被当成地址或网络故障。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        """执行测试所需的辅助操作。"""
        return httpx.Response(
            429,
            json={
                "error": {
                    "code": "insufficient_quota",
                    "message": (
                        "You exceeded your current quota, "
                        "please check your plan and billing details."
                    ),
                }
            },
        )

    clock = FakeClock()
    client = _client(handler)
    gateway = _gateway(client, max_retries=1, jitter=lambda: 1.0, clock=clock)
    try:
        with pytest.raises(DomainError) as error:
            await gateway.embed(["a"])
    finally:
        await gateway.close()

    failure = error.value.failure
    assert failure.code == "EMBEDDING_QUOTA_EXCEEDED"
    assert failure.retryable is True
    assert "insufficient_quota" in failure.message
