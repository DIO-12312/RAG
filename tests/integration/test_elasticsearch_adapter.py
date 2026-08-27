"""Integration tests against a real Elasticsearch node."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from elasticsearch import AsyncElasticsearch

from rag_mvp.adapters.search_engine.elasticsearch import ElasticsearchSearchEngine
from rag_mvp.domain.ids import es_record_id
from rag_mvp.domain.models import Chunk, Locator
from rag_mvp.ports.search_engine import IndexedChunk, SearchRequest


def _indexed(
    *,
    dataset_id: str,
    document_id: str,
    version: int,
    chunk_id: str,
    content: str,
    vector: tuple[float, float, float],
    category: str,
) -> IndexedChunk:
    chunk = Chunk(
        id=chunk_id,
        document_id=document_id,
        index_version=version,
        ordinal=0,
        content_with_weight=content,
        content_sha256=(chunk_id[0] if chunk_id else "c") * 64,
        source_name=f"{document_id}.md",
        locator=Locator(start_line=1, end_line=2, metadata={"section": category}),
        metadata={"category": category},
    )
    return IndexedChunk(
        record_id=es_record_id(document_id, version, chunk_id),
        dataset_id=dataset_id,
        chunk=chunk,
        vector=vector,
    )


@pytest_asyncio.fixture
async def elasticsearch_search() -> AsyncIterator[
    tuple[ElasticsearchSearchEngine, AsyncElasticsearch]
]:
    url = os.environ.get("RAG_TEST_ELASTICSEARCH_URL", "http://127.0.0.1:9200")
    index_name = f"rag-test-{uuid.uuid4().hex}"
    client = AsyncElasticsearch(url, request_timeout=10)
    search = ElasticsearchSearchEngine(client, index_name, embedding_dimension=3)
    try:
        await search.ensure_index()
        yield search, client
    finally:
        await client.indices.delete(index=index_name, ignore_unavailable=True)
        await search.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_es_upsert_dense_bm25_isolation_and_metadata_filters(
    elasticsearch_search: tuple[ElasticsearchSearchEngine, AsyncElasticsearch],
) -> None:
    search, client = elasticsearch_search
    guide_v1 = _indexed(
        dataset_id="dataset-1",
        document_id="document-1",
        version=1,
        chunk_id="aaaaaaaaaaaaaaaa",
        content="python semantic retrieval exacttoken",
        vector=(1.0, 0.0, 0.0),
        category="guide",
    )
    guide_v2 = _indexed(
        dataset_id="dataset-1",
        document_id="document-1",
        version=2,
        chunk_id="bbbbbbbbbbbbbbbb",
        content="advanced retrieval exacttoken",
        vector=(0.9, 0.1, 0.0),
        category="guide",
    )
    recipe = _indexed(
        dataset_id="dataset-1",
        document_id="document-2",
        version=1,
        chunk_id="cccccccccccccccc",
        content="cooking bread exactfood",
        vector=(0.0, 1.0, 0.0),
        category="recipe",
    )
    isolated = _indexed(
        dataset_id="dataset-2",
        document_id="document-3",
        version=1,
        chunk_id="dddddddddddddddd",
        content="python exacttoken isolated",
        vector=(1.0, 0.0, 0.0),
        category="guide",
    )
    tied_guide = _indexed(
        dataset_id="dataset-1",
        document_id="document-0",
        version=1,
        chunk_id="eeeeeeeeeeeeeeee",
        content="python semantic retrieval exacttoken",
        vector=(1.0, 0.0, 0.0),
        category="guide",
    )

    await search.upsert_chunks([guide_v1, guide_v2, recipe, isolated, tied_guide, guide_v1])

    count = await client.count(index=search.index_name)
    dense = await search.dense_search(
        SearchRequest(
            dataset_id="dataset-1",
            top_k=5,
            query_vector=(1.0, 0.0, 0.0),
            filters={"category": "guide"},
        )
    )
    sparse = await search.sparse_search(
        SearchRequest(dataset_id="dataset-1", top_k=5, query="exacttoken")
    )
    recipe_only = await search.sparse_search(
        SearchRequest(
            dataset_id="dataset-1",
            top_k=5,
            query="exactfood",
            filters={"category": "recipe"},
        )
    )

    assert count["count"] == 5
    assert [candidate.chunk.id for candidate in dense[:3]] == [
        tied_guide.chunk.id,
        guide_v1.chunk.id,
        guide_v2.chunk.id,
    ]
    assert {candidate.chunk.id for candidate in sparse} == {
        tied_guide.chunk.id,
        guide_v1.chunk.id,
        guide_v2.chunk.id,
    }
    assert [candidate.chunk.id for candidate in recipe_only] == [recipe.chunk.id]
    assert all(candidate.dataset_id == "dataset-1" for candidate in (*dense, *sparse))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_es_version_and_document_delete_are_idempotent(
    elasticsearch_search: tuple[ElasticsearchSearchEngine, AsyncElasticsearch],
) -> None:
    search, client = elasticsearch_search
    version_1 = _indexed(
        dataset_id="dataset-1",
        document_id="document-1",
        version=1,
        chunk_id="aaaaaaaaaaaaaaaa",
        content="version one",
        vector=(1.0, 0.0, 0.0),
        category="guide",
    )
    version_2 = _indexed(
        dataset_id="dataset-1",
        document_id="document-1",
        version=2,
        chunk_id="bbbbbbbbbbbbbbbb",
        content="version two",
        vector=(0.9, 0.1, 0.0),
        category="guide",
    )
    await search.upsert_chunks([version_1, version_2])

    await search.delete_document_version("document-1", 1)
    await search.delete_document_version("document-1", 1)
    after_version = await client.count(index=search.index_name)
    assert after_version["count"] == 1

    await search.delete_document("document-1")
    await search.delete_document("document-1")
    after_document = await client.count(index=search.index_name)
    assert after_document["count"] == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_es_dataset_delete_is_idempotent_and_isolated(
    elasticsearch_search: tuple[ElasticsearchSearchEngine, AsyncElasticsearch],
) -> None:
    search, client = elasticsearch_search
    first = _indexed(
        dataset_id="dataset-1",
        document_id="document-1",
        version=1,
        chunk_id="aaaaaaaaaaaaaaaa",
        content="delete me",
        vector=(1.0, 0.0, 0.0),
        category="guide",
    )
    isolated = _indexed(
        dataset_id="dataset-2",
        document_id="document-2",
        version=1,
        chunk_id="bbbbbbbbbbbbbbbb",
        content="keep me",
        vector=(0.0, 1.0, 0.0),
        category="guide",
    )
    await search.upsert_chunks([first, isolated])

    await search.delete_dataset("dataset-1")
    await search.delete_dataset("dataset-1")

    count = await client.count(index=search.index_name)
    remaining = await search.sparse_search(
        SearchRequest(dataset_id="dataset-2", top_k=3, query="keep")
    )
    assert count["count"] == 1
    assert [candidate.chunk.id for candidate in remaining] == [isolated.chunk.id]
