"""兼容 OpenAI 的向量模型适配器：有限重试并严格校验响应维度。"""

from __future__ import annotations

import asyncio
import math
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import structlog

from rag_mvp.domain.errors import DomainError, DomainFailure

# 真实模型网关的限流窗口以秒计，0.1s 级别的退避在 429 面前等于没有退避：
# 大文档（数千 Chunk、上百批次）必然在同一个窗口内反复超限，
# 最终把「暂时不可用」放大成整个文档摄取失败。
INITIAL_RETRY_DELAY_SECONDS = 1.0
MAX_RETRY_DELAY_SECONDS = 30.0
_PROVIDER_DETAIL_LIMIT = 200

_LOGGER = structlog.get_logger("rag_mvp.model")


class OpenAICompatibleModelGateway:
    """Call an OpenAI-compatible embedding endpoint without leaking provider details."""

    # 初始化该对象的依赖、配置或受控资源。
    def __init__(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        model: str,
        dimension: int,
        batch_size: int,
        max_retries: int,
        max_concurrency: int,
        retry_base_delay_seconds: float = INITIAL_RETRY_DELAY_SECONDS,
        retry_max_delay_seconds: float = MAX_RETRY_DELAY_SECONDS,
        jitter: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        normalized_endpoint = endpoint.strip().rstrip("/")
        if not normalized_endpoint:
            raise ValueError("endpoint must not be empty")
        if not normalized_endpoint.endswith("/embeddings"):
            normalized_endpoint = f"{normalized_endpoint}/embeddings"
        if not model.strip():
            raise ValueError("model must not be empty")
        if dimension < 1:
            raise ValueError("dimension must be at least 1")
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        if max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        if retry_base_delay_seconds <= 0:
            raise ValueError("retry_base_delay_seconds must be positive")
        if retry_max_delay_seconds < retry_base_delay_seconds:
            raise ValueError("retry_max_delay_seconds must not be smaller than the base delay")

        self._client = client
        self._endpoint = normalized_endpoint
        self._model = model
        self._dimension = dimension
        self._batch_size = batch_size
        self._max_retries = max_retries
        self._max_concurrency = max_concurrency
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._retry_max_delay_seconds = retry_max_delay_seconds
        self._jitter = jitter or (lambda: random.uniform(0.75, 1.25))
        self._sleep = sleep or asyncio.sleep
        self._monotonic = monotonic or time.monotonic
        # 提供方返回 429 时，整个文档的批次都要一起停下来，
        # 否则并发中的其他批次会在同一限流窗口里继续触发 429。
        self._pause_until = 0.0
        self._pause_lock = asyncio.Lock()

    # 返回不暴露敏感配置的调试表示。
    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(model={self._model!r}, dimension={self._dimension}, "
            f"batch_size={self._batch_size}, max_retries={self._max_retries}, "
            f"max_concurrency={self._max_concurrency})"
        )

    # 实现 embed 对应的局部职责。
    async def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        """Embed inputs in bounded batches while preserving original order."""

        batches = [
            texts[offset : offset + self._batch_size]
            for offset in range(0, len(texts), self._batch_size)
        ]
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def embed_bounded(batch: list[str]) -> list[tuple[float, ...]]:
            async with semaphore:
                return await self._embed_batch(batch)

        # gather 保持输入 batch 的顺序，因此并发不会改变 Chunk 与向量的对应关系。
        embedded_batches = await asyncio.gather(*(embed_bounded(batch) for batch in batches))
        return [vector for batch in embedded_batches for vector in batch]

    # 实现 rerank 对应的局部职责。
    async def rerank(self, query: str, passages: list[str]) -> list[float]:
        """Report explicit degradation until a separate rerank endpoint is configured."""

        del query, passages
        raise DomainError(
            DomainFailure(
                "RERANK_UNAVAILABLE",
                "rerank provider is not configured",
                retryable=True,
            )
        )

    # 按资源所有权顺序关闭底层连接或句柄。
    async def close(self) -> None:
        """Close the owned HTTP client."""

        await self._client.aclose()

    # 内部辅助：完成 embed_batch 所需的局部转换或校验。
    async def _embed_batch(self, texts: list[str]) -> list[tuple[float, ...]]:
        for attempt in range(self._max_retries + 1):
            await self._wait_for_provider_pause()
            try:
                response = await self._client.post(
                    self._endpoint,
                    json={"model": self._model, "input": texts},
                )
            except httpx.RequestError as exc:
                if attempt < self._max_retries:
                    await self._backoff(attempt, None)
                    continue
                raise self._unavailable(f"transport_error={type(exc).__name__}") from exc

            if response.status_code in {401, 403}:
                raise DomainError(
                    DomainFailure(
                        "EMBEDDING_AUTH_FAILED",
                        "embedding provider authentication failed",
                        retryable=False,
                    )
                )
            if response.status_code == 429 or response.status_code >= 500:
                retry_after = _retry_after_seconds(response)
                detail = _provider_detail(response)
                log = _LOGGER.warning if attempt >= self._max_retries else _LOGGER.info
                log(
                    "embedding_request_retry",
                    status=response.status_code,
                    attempt=attempt + 1,
                    max_attempts=self._max_retries + 1,
                    provider_detail=detail,
                    retry_after_seconds=retry_after,
                )
                if attempt < self._max_retries:
                    if response.status_code == 429:
                        await self._pause_all(retry_after)
                    await self._backoff(attempt, retry_after)
                    continue
                raise self._unavailable(
                    f"status={response.status_code}",
                    detail=detail,
                )
            if response.status_code == 400 and len(texts) > 1:
                midpoint = len(texts) // 2
                left = await self._embed_batch(texts[:midpoint])
                right = await self._embed_batch(texts[midpoint:])
                return [*left, *right]
            if response.status_code < 200 or response.status_code >= 300:
                raise DomainError(
                    DomainFailure(
                        "EMBEDDING_REQUEST_REJECTED",
                        "embedding provider rejected the request",
                        retryable=False,
                    )
                )
            return self._parse_response(response, len(texts))

        raise RuntimeError("embedding retry loop terminated unexpectedly")

    # 内部辅助：完成 backoff 所需的局部转换或校验。
    async def _backoff(self, attempt: int, retry_after: float | None) -> None:
        if retry_after is None:
            delay = min(
                self._retry_base_delay_seconds * (2**attempt),
                self._retry_max_delay_seconds,
            )
        else:
            delay = min(retry_after, self._retry_max_delay_seconds)
        await self._sleep(delay * self._jitter())

    # 让同一文档内所有并发批次共享提供方给出的节流窗口。
    async def _pause_all(self, retry_after: float | None) -> None:
        delay = min(
            retry_after if retry_after is not None else self._retry_base_delay_seconds,
            self._retry_max_delay_seconds,
        )
        async with self._pause_lock:
            deadline = self._monotonic() + delay
            self._pause_until = max(self._pause_until, deadline)

    # 在提供方节流窗口内等待，避免继续加压触发更多 429。
    async def _wait_for_provider_pause(self) -> None:
        remaining = self._pause_until - self._monotonic()
        if remaining > 0:
            await self._sleep(min(remaining, self._retry_max_delay_seconds))

    # 内部辅助：完成 parse_response 所需的局部转换或校验。
    def _parse_response(
        self,
        response: httpx.Response,
        expected_count: int,
    ) -> list[tuple[float, ...]]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise self._invalid_response() from exc
        if not isinstance(payload, Mapping):
            raise self._invalid_response()
        if payload.get("object") != "list":
            raise self._invalid_response()
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != expected_count:
            raise self._invalid_response()

        ordered: list[tuple[float, ...] | None] = [None] * expected_count
        for item in data:
            if not isinstance(item, Mapping):
                raise self._invalid_response()
            index = item.get("index")
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or index < 0
                or index >= expected_count
                or ordered[index] is not None
            ):
                raise self._invalid_response()
            vector = self._parse_vector(item.get("embedding"))
            ordered[index] = vector

        if any(vector is None for vector in ordered):
            raise self._invalid_response()
        return [vector for vector in ordered if vector is not None]

    # 内部辅助：完成 parse_vector 所需的局部转换或校验。
    def _parse_vector(self, raw_vector: Any) -> tuple[float, ...]:
        if not isinstance(raw_vector, list):
            raise self._invalid_response()
        if len(raw_vector) != self._dimension:
            raise DomainError(
                DomainFailure(
                    "EMBEDDING_DIMENSION_MISMATCH",
                    "embedding vector does not match the configured dimension",
                    retryable=False,
                )
            )
        vector: list[float] = []
        for value in raw_vector:
            if not isinstance(value, int | float) or isinstance(value, bool):
                raise self._invalid_response()
            converted = float(value)
            if not math.isfinite(converted):
                raise self._invalid_response()
            vector.append(converted)
        return tuple(vector)

    @staticmethod
    # 内部辅助：完成 invalid_response 所需的局部转换或校验。
    def _invalid_response() -> DomainError:
        return DomainError(
            DomainFailure(
                "EMBEDDING_RESPONSE_INVALID",
                "embedding provider returned an invalid response",
                retryable=False,
            )
        )

    @staticmethod
    # 内部辅助：完成 unavailable 所需的局部转换或校验。
    def _unavailable(*facts: str, detail: str = "") -> DomainError:
        # 失败信息必须带上提供方的状态与错误码，否则运维只能看到
        # 「服务暂时不可用」，无法区分限流、超时还是配额耗尽。
        message = " ".join(
            part for part in ("embedding provider is temporarily unavailable", *facts) if part
        )
        if detail:
            message = f"{message} ({detail})"
        return DomainError(
            DomainFailure(
                "EMBEDDING_UNAVAILABLE",
                message,
                retryable=True,
            )
        )


# 解析提供方给出的 Retry-After（秒数或 HTTP 日期），无法识别时返回 None。
def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw or not raw.strip():
        return None
    candidate = raw.strip()
    try:
        seconds = float(candidate)
    except ValueError:
        try:
            moment = parsedate_to_datetime(candidate)
        except (TypeError, ValueError):
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        seconds = (moment - datetime.now(UTC)).total_seconds()
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


# 只提取提供方的错误码，避免把请求正文、输入文本或凭据回显到日志与失败信息。
def _provider_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""
    if not isinstance(payload, Mapping):
        return ""
    error = payload.get("error")
    code = error.get("code") if isinstance(error, Mapping) else payload.get("code")
    if isinstance(code, str) and code.strip():
        return code.strip()[:_PROVIDER_DETAIL_LIMIT]
    return ""
