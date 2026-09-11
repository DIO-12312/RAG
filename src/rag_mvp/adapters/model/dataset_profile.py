"""Dataset-scoped encrypted provider snapshots, never deployment model credentials."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import socket
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from rag_mvp.adapters.model.openai_compatible import OpenAICompatibleModelGateway
from rag_mvp.adapters.model.rerank import rerank_passages
from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.domain.models import Dataset
from rag_mvp.ports.model import ModelGateway

PROFILE_AAD = b"rag/embedding-profile/v1"


class PublicEndpointTransport(httpx.AsyncBaseTransport):
    """Resolve then connect to a checked public IP, preserving HTTPS SNI/Host."""

    def __init__(self) -> None:
        self._transport = httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if request.url.scheme != "https" or request.url.userinfo:
            raise httpx.ConnectError("model endpoint requires HTTPS", request=request)
        try:
            addresses = await asyncio.get_running_loop().getaddrinfo(
                host, request.url.port or 443, type=socket.SOCK_STREAM
            )
        except socket.gaierror:
            raise httpx.ConnectError("model endpoint DNS unavailable", request=request) from None
        ips = [str(item[4][0]) for item in addresses]
        # Local TUN proxies may synthesize benchmark-range addresses. Resolve
        # those through a pinned HTTPS public resolver; never allow the fake IP.
        if ips and all(
            ipaddress.ip_address(ip) in ipaddress.ip_network("198.18.0.0/15") for ip in ips
        ):
            async with httpx.AsyncClient(
                timeout=10, trust_env=False, follow_redirects=False
            ) as resolver:
                response = await resolver.get(
                    "https://1.1.1.1/dns-query",
                    params={"name": host, "type": "A"},
                    headers={"accept": "application/dns-json"},
                )
                try:
                    response.raise_for_status()
                    ips = [
                        record["data"]
                        for record in response.json().get("Answer", [])
                        if record.get("type") == 1
                    ]
                except (ValueError, KeyError, TypeError, httpx.HTTPStatusError):
                    raise httpx.ConnectError(
                        "public model DNS unavailable", request=request
                    ) from None
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise httpx.ConnectError("model endpoint must be public", request=request)
        request.headers["Host"] = request.url.netloc.decode("ascii")
        request.extensions["sni_hostname"] = host
        request.url = request.url.copy_with(host=ips[0])
        return await self._transport.handle_async_request(request)

    async def aclose(self) -> None:
        await self._transport.aclose()


class DatasetProfileGateway:
    def __init__(
        self,
        key_file: Path,
        dataset: Dataset | None = None,
        rerank_profile: str = "",
        rerank_dataset_id: str = "",
    ) -> None:
        self._key_file = key_file
        self._dataset = dataset
        self._rerank_profile = rerank_profile
        self._rerank_dataset_id = rerank_dataset_id

    def for_rerank(self, encrypted_profile: str, dataset_id: str) -> ModelGateway:
        return DatasetProfileGateway(self._key_file, self._dataset, encrypted_profile, dataset_id)

    def for_dataset(self, dataset: Dataset) -> ModelGateway:
        return DatasetProfileGateway(self._key_file, dataset)

    async def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        dataset = self._dataset
        if dataset is None or not dataset.encrypted_embedding_profile:
            raise DomainError(
                DomainFailure(
                    "EMBEDDING_NOT_CONFIGURED",
                    "configure embedding in personal settings and bind the dataset",
                )
            )
        try:
            key = base64.b64decode(self._key_file.read_text().strip(), validate=True)
            sealed = base64.b64decode(dataset.encrypted_embedding_profile, validate=True)
            config = json.loads(AESGCM(key).decrypt(sealed[:12], sealed[12:], PROFILE_AAD))
            if (
                config["modelName"] != dataset.embedding_model
                or config["embeddingDimension"] != dataset.embedding_dimension
            ):
                raise ValueError("snapshot mismatch")
            endpoint = config["baseUrl"]
            api_key = config["apiKey"]
            timeout = float(config["timeoutSeconds"])
            if (
                not isinstance(endpoint, str)
                or not isinstance(api_key, str)
                or not api_key
                or not 1 <= timeout <= 300
            ):
                raise ValueError("invalid snapshot")
        except Exception:
            raise DomainError(
                DomainFailure(
                    "EMBEDDING_PROFILE_INVALID", "embedding snapshot unavailable or invalid"
                )
            ) from None
        async with httpx.AsyncClient(
            transport=PublicEndpointTransport(),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            model = OpenAICompatibleModelGateway(
                client, endpoint, dataset.embedding_model, dataset.embedding_dimension, 32, 2
            )
            return await model.embed(texts)

    async def rerank(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        try:
            key = base64.b64decode(self._key_file.read_text().strip(), validate=True)
            sealed = base64.b64decode(self._rerank_profile, validate=True)
            aad = ("rag/rerank-profile/v1/" + self._rerank_dataset_id).encode()
            config = json.loads(AESGCM(key).decrypt(sealed[:12], sealed[12:], aad))
            endpoint, name, api_key = config["baseUrl"], config["modelName"], config["apiKey"]
            timeout = float(config["timeoutSeconds"])
            if (
                not all(isinstance(v, str) and v.strip() for v in (endpoint, name, api_key))
                or not 1 <= timeout <= 300
            ):
                raise ValueError("invalid profile")
        except Exception:
            raise DomainError(
                DomainFailure(
                    "RERANK_PROFILE_INVALID",
                    "rerank configuration unavailable or invalid",
                )
            ) from None
        async with httpx.AsyncClient(
            transport=PublicEndpointTransport(),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            return await rerank_passages(client, endpoint, name, query, passages)
