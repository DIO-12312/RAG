"""OpenAI-compatible embedding adapter with bounded retries and strict validation."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from typing import Any

import httpx

from rag_mvp.domain.errors import DomainError, DomainFailure

INITIAL_RETRY_DELAY_SECONDS = 0.1


class OpenAICompatibleModelGateway:
    """Call an OpenAI-compatible embedding endpoint without leaking provider details."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        model: str,
        dimension: int,
        batch_size: int,
        max_retries: int,
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

        self._client = client
        self._endpoint = normalized_endpoint
        self._model = model
        self._dimension = dimension
        self._batch_size = batch_size
        self._max_retries = max_retries

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(model={self._model!r}, dimension={self._dimension}, "
            f"batch_size={self._batch_size}, max_retries={self._max_retries})"
        )

    async def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        """Embed inputs in bounded batches while preserving original order."""

        vectors: list[tuple[float, ...]] = []
        for offset in range(0, len(texts), self._batch_size):
            vectors.extend(await self._embed_batch(texts[offset : offset + self._batch_size]))
        return vectors

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

    async def close(self) -> None:
        """Close the owned HTTP client."""

        await self._client.aclose()

    async def _embed_batch(self, texts: list[str]) -> list[tuple[float, ...]]:
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.post(
                    self._endpoint,
                    json={"model": self._model, "input": texts},
                )
            except httpx.RequestError as exc:
                if attempt < self._max_retries:
                    await self._backoff(attempt)
                    continue
                raise self._unavailable() from exc

            if response.status_code in {401, 403}:
                raise DomainError(
                    DomainFailure(
                        "EMBEDDING_AUTH_FAILED",
                        "embedding provider authentication failed",
                        retryable=False,
                    )
                )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < self._max_retries:
                    await self._backoff(attempt)
                    continue
                raise self._unavailable()
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

    async def _backoff(self, attempt: int) -> None:
        await asyncio.sleep(INITIAL_RETRY_DELAY_SECONDS * (2**attempt))

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
    def _invalid_response() -> DomainError:
        return DomainError(
            DomainFailure(
                "EMBEDDING_RESPONSE_INVALID",
                "embedding provider returned an invalid response",
                retryable=False,
            )
        )

    @staticmethod
    def _unavailable() -> DomainError:
        return DomainError(
            DomainFailure(
                "EMBEDDING_UNAVAILABLE",
                "embedding provider is temporarily unavailable",
                retryable=True,
            )
        )
