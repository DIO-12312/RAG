"""Encrypted dataset profiles select isolated providers and reject unsafe endpoints."""

import asyncio
import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from rag_mvp.adapters.model import dataset_profile as module
from rag_mvp.domain.errors import DomainError
from rag_mvp.domain.models import Dataset


@pytest.mark.asyncio
async def test_profiles_keep_provider_keys_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = bytes(range(32))
    path = tmp_path / "key"
    path.write_text(base64.b64encode(key).decode())
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(
            (request.url.host, request.headers["authorization"], json.loads(request.content))
        )
        return httpx.Response(
            200, json={"object": "list", "data": [{"index": 0, "embedding": [1.0, 0.0]}]}
        )

    monkeypatch.setattr(module, "PublicEndpointTransport", lambda: httpx.MockTransport(handler))
    gateway = module.DatasetProfileGateway(path)
    for index in range(2):
        config = {
            "modelName": "test",
            "embeddingDimension": 2,
            "baseUrl": f"https://p{index}.example/v1",
            "apiKey": f"key-{index}",
            "timeoutSeconds": 10,
        }
        nonce = bytes([index]) * 12
        sealed = base64.b64encode(
            nonce
            + AESGCM(key).encrypt(nonce, json.dumps(config).encode(), b"rag/embedding-profile/v1")
        ).decode()
        dataset = Dataset(
            str(index), "test", "test", 2, datetime.now(UTC), encrypted_embedding_profile=sealed
        )
        assert await gateway.for_dataset(dataset).embed(["hello"]) == [(1.0, 0.0)]
        assert sealed not in repr(dataset)
    assert requests == [
        ("p0.example", "Bearer key-0", {"model": "test", "input": ["hello"]}),
        ("p1.example", "Bearer key-1", {"model": "test", "input": ["hello"]}),
    ]


@pytest.mark.asyncio
async def test_invalid_profile_fails_without_exposing_secret(tmp_path: Path) -> None:
    dataset = Dataset(
        "d", "test", "test", 2, datetime.now(UTC), encrypted_embedding_profile="secret-sentinel"
    )
    with pytest.raises(DomainError) as caught:
        await (
            module.DatasetProfileGateway(tmp_path / "missing").for_dataset(dataset).embed(["hello"])
        )
    assert "secret-sentinel" not in str(caught.value)
    with pytest.raises(DomainError):
        await module.DatasetProfileGateway(tmp_path / "missing").embed(["hello"])


@pytest.mark.asyncio
@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"])
async def test_endpoint_blocks_private_resolution(
    address: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop, "getaddrinfo", AsyncMock(return_value=[(0, 0, 0, "", (address, 443))])
    )
    transport = module.PublicEndpointTransport()
    try:
        with pytest.raises(httpx.ConnectError, match="must be public"):
            await transport.handle_async_request(
                httpx.Request("POST", "https://model.example/v1/embeddings")
            )
    finally:
        await transport.aclose()


@pytest.mark.asyncio
async def test_fake_ip_resolution_uses_public_dns_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "getaddrinfo",
        AsyncMock(return_value=[(0, 0, 0, "", ("198.18.1.115", 443))]),
    )
    seen = []

    async def doh(self: httpx.AsyncClient, url: str, **kwargs: object) -> httpx.Response:
        assert url == "https://1.1.1.1/dns-query"
        return httpx.Response(
            200,
            json={"Answer": [{"type": 1, "data": "93.184.215.14"}]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", doh)

    async def send(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers["Host"], request.extensions["sni_hostname"]))
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", send)
    transport = module.PublicEndpointTransport()
    try:
        await transport.handle_async_request(
            httpx.Request("POST", "https://model.example/embeddings")
        )
        assert seen == [("93.184.215.14", "model.example", "model.example")]
    finally:
        await transport.aclose()
