"""Request-scoped reranking: encrypted configuration and indexed provider scores."""

import asyncio
import base64
import json
import socket
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from rag_mvp.adapters.model import dataset_profile
from rag_mvp.domain.errors import DomainError


@pytest.mark.asyncio
async def test_public_endpoint_transport_maps_temporary_dns_failure_to_connect_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(*_args: object, **_kwargs: object) -> list[object]:
        raise socket.gaierror(socket.EAI_AGAIN, "temporary DNS failure")

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", unavailable)
    transport = dataset_profile.PublicEndpointTransport()
    request = httpx.Request("POST", "https://provider.test/rerank")
    try:
        with pytest.raises(httpx.ConnectError, match="model endpoint DNS unavailable"):
            await transport.handle_async_request(request)
    finally:
        await transport.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    ["ok", "duplicate", "missing", "nan", "unauthorized", "unavailable", "wrong_dataset", "empty"],
)
async def test_rerank_profile_validates_scores_and_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    key = b"a" * 32
    key_file = tmp_path / "key"
    key_file.write_text(base64.b64encode(key).decode())
    nonce = b"b" * 12
    config = {
        "baseUrl": "https://provider.test/v1/",
        "modelName": "reranker",
        "apiKey": "test-secret",
        "timeoutSeconds": 30,
    }
    sealed = base64.b64encode(
        nonce
        + AESGCM(key).encrypt(
            nonce, json.dumps(config).encode(), b"rag/rerank-profile/v1/dataset-1"
        )
    ).decode()
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert str(request.url) == "https://provider.test/v1/rerank"
        assert request.headers["authorization"] == "Bearer test-secret"
        assert json.loads(request.content) == {
            "model": "reranker",
            "query": "question",
            "documents": ["first", "second"],
            "top_n": 2,
            "return_documents": False,
        }
        if mode in {"unauthorized", "unavailable"}:
            return httpx.Response(401 if mode == "unauthorized" else 503, text="test-secret")
        results = [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.2}]
        if mode == "duplicate":
            results[1]["index"] = 1
        if mode == "missing":
            results.pop()
        if mode == "nan":
            results[0]["relevance_score"] = "NaN"
        return httpx.Response(200, json={"results": results})

    monkeypatch.setattr(
        dataset_profile, "PublicEndpointTransport", lambda: httpx.MockTransport(respond)
    )
    gateway = dataset_profile.DatasetProfileGateway(key_file).for_rerank(
        sealed, "other" if mode == "wrong_dataset" else "dataset-1"
    )
    if mode in {"ok", "empty"}:
        assert await gateway.rerank("question", [] if mode == "empty" else ["first", "second"]) == (
            [] if mode == "empty" else [0.2, 0.9]
        )
    else:
        with pytest.raises(DomainError) as caught:
            await gateway.rerank("question", ["first", "second"])
        assert "test-secret" not in str(caught.value)
        assert caught.value.failure.retryable is (
            mode in {"duplicate", "missing", "nan", "unavailable"}
        )
    if mode in {"wrong_dataset", "empty"}:
        assert not requests
