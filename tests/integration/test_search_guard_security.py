"""Search Guard HTTPS 身份与最小权限的真实 Docker 验证。"""

from __future__ import annotations

import os

import pytest
from elasticsearch import ApiError, AsyncElasticsearch, ConnectionError

from tests.integration.test_elasticsearch_adapter import _client_from_environment


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_guard_rejects_anonymous_http_and_bad_credentials() -> None:
    """去掉 Basic、改错密码或降级 HTTP 都不得得到 ES 响应。"""

    secure = _client_from_environment("RAG_TEST")
    anonymous = AsyncElasticsearch(
        os.environ["RAG_TEST_ELASTICSEARCH_URL"],
        ca_certs=os.environ["RAG_TEST_ELASTICSEARCH_CA_CERT"],
        verify_certs=True,
    )
    bad = AsyncElasticsearch(
        os.environ["RAG_TEST_ELASTICSEARCH_URL"],
        basic_auth=(os.environ["RAG_TEST_ELASTICSEARCH_USERNAME"], "wrong-password"),
        ca_certs=os.environ["RAG_TEST_ELASTICSEARCH_CA_CERT"],
        verify_certs=True,
    )
    plaintext = AsyncElasticsearch("http://elasticsearch:9200", request_timeout=10)
    try:
        assert await secure.ping() is True
        with pytest.raises(ApiError) as anonymous_error:
            await anonymous.info()
        assert anonymous_error.value.status_code == 401
        with pytest.raises(ApiError) as bad_error:
            await bad.info()
        assert bad_error.value.status_code == 401
        with pytest.raises(ConnectionError):
            await plaintext.info()
    finally:
        await secure.close()
        await anonymous.close()
        await bad.close()
        await plaintext.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rag_identity_cannot_manage_search_guard_or_other_indices() -> None:
    """运行身份只能访问 RAG 索引，不能读取管理 API 或创建其他索引。"""

    client = _client_from_environment("RAG_TEST")
    try:
        with pytest.raises(ApiError) as other_index:
            await client.indices.create(index="forbidden-index")
        assert other_index.value.status_code == 403
        with pytest.raises(ApiError) as management:
            await client.perform_request("GET", "/_searchguard/api/roles")
        assert management.value.status_code == 403
    finally:
        await client.close()
