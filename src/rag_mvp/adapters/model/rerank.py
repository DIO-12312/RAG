"""Dedicated /rerank provider protocol; return scores in input order."""

import math

import httpx

from rag_mvp.domain.errors import DomainError, DomainFailure


async def rerank_passages(
    client: httpx.AsyncClient, endpoint: str, model: str, query: str, passages: list[str]
) -> list[float]:
    if not passages:
        return []
    url = endpoint.rstrip("/")
    if not url.endswith("/rerank"):
        url += "/rerank"
    try:
        response = await client.post(
            url,
            json={
                "model": model,
                "query": query,
                "documents": passages,
                "top_n": len(passages),
                "return_documents": False,
            },
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
        results = response.json()["results"]
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
