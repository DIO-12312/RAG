"""Dedicated /rerank provider protocol; return scores in input order."""

import math
from enum import StrEnum
from typing import Any

import httpx

from rag_mvp.domain.errors import DomainError, DomainFailure

DASHSCOPE_NATIVE_SUFFIX = "/api/v1/services/rerank/text-rerank/text-rerank"


class RerankProtocol(StrEnum):
    FLAT = "flat"
    DASHSCOPE_NATIVE = "dashscope_native"


async def rerank_passages(
    client: httpx.AsyncClient, endpoint: str, model: str, query: str, passages: list[str]
) -> list[float]:
    if not passages:
        return []
    url, protocol = _normalize_endpoint(endpoint)
    try:
        response = await client.post(
            url,
            json=_request_payload(protocol, model, query, passages),
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        raise DomainError(
            DomainFailure(
                "RERANK_UNAVAILABLE",
                "rerank provider rejected request",
                retryable=status in {408, 429} or status >= 500,
            )
        ) from None
    except httpx.RequestError:
        raise DomainError(
            DomainFailure(
                "RERANK_UNAVAILABLE",
                "rerank provider unavailable",
                retryable=True,
            )
        ) from None
    try:
        results = _response_results(response.json())
        if not isinstance(results, list) or len(results) != len(passages):
            raise ValueError("missing scores")
        scores = [0.0] * len(passages)
        seen: set[int] = set()
        for result in results:
            index, score = result["index"], result["relevance_score"]
            if (
                type(index) is not int
                or not 0 <= index < len(passages)
                or index in seen
                or type(score) not in {int, float}
                or not math.isfinite(score)
            ):
                raise ValueError("invalid score")
            seen.add(index)
            scores[index] = float(score)
        return scores
    except (ValueError, KeyError, TypeError, OverflowError):
        raise DomainError(
            DomainFailure(
                "RERANK_RESPONSE_INVALID",
                "rerank provider returned invalid scores",
                retryable=True,
            )
        ) from None


def _normalize_endpoint(endpoint: str) -> tuple[str, RerankProtocol]:
    """Preserve complete provider URLs and append only protocol-specific endpoints."""

    url = endpoint.strip().rstrip("/")
    if url.endswith(DASHSCOPE_NATIVE_SUFFIX):
        return url, RerankProtocol.DASHSCOPE_NATIVE
    if url.endswith("/compatible-api/v1"):
        return f"{url}/reranks", RerankProtocol.FLAT
    if url.endswith(("/rerank", "/reranks")):
        return url, RerankProtocol.FLAT
    return f"{url}/rerank", RerankProtocol.FLAT


def _request_payload(
    protocol: RerankProtocol,
    model: str,
    query: str,
    passages: list[str],
) -> dict[str, Any]:
    if protocol is RerankProtocol.DASHSCOPE_NATIVE:
        return {
            "model": model,
            "input": {"query": query, "documents": passages},
            "parameters": {"top_n": len(passages), "return_documents": False},
        }
    return {
        "model": model,
        "query": query,
        "documents": passages,
        "top_n": len(passages),
        "return_documents": False,
    }


def _response_results(payload: object) -> object:
    if not isinstance(payload, dict):
        raise ValueError("response must be an object")
    results = payload.get("results")
    if results is not None:
        return results
    output = payload.get("output")
    if isinstance(output, dict):
        return output.get("results")
    raise ValueError("missing results")
