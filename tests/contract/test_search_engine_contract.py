from __future__ import annotations

import pytest

from rag_mvp.domain.models import Chunk, Locator
from rag_mvp.ports.search_engine import IndexedChunk, SearchRequest
from tests.fakes.search_engine import FakeSearchEngine


def _indexed(content: str, chunk_id: str, vector: tuple[float, ...]) -> IndexedChunk:
    chunk = Chunk(
        id=chunk_id,
        document_id="document-1",
        index_version=1,
        ordinal=0,
        content_with_weight=content,
        content_sha256="c" * 64,
        source_name="guide.txt",
        locator=Locator(start_line=1, end_line=1),
        metadata={"category": "guide"},
    )
    return IndexedChunk(
        record_id=f"document-1:1:{chunk_id}",
        dataset_id="dataset-1",
        chunk=chunk,
        vector=vector,
    )


@pytest.mark.asyncio
async def test_search_upsert_is_idempotent_and_dense_sparse_are_separate() -> None:
    search = FakeSearchEngine()
    relevant = _indexed("python retrieval guide", "chunk-a", (1.0, 0.0))
    other = _indexed("cooking notes", "chunk-b", (0.0, 1.0))
    await search.upsert_chunks([relevant, other, relevant])

    dense = await search.dense_search(
        SearchRequest(
            dataset_id="dataset-1",
            top_k=2,
            query_vector=(1.0, 0.0),
            filters={"category": "guide"},
        )
    )
    sparse = await search.sparse_search(
        SearchRequest(dataset_id="dataset-1", top_k=2, query="python guide")
    )

    assert search.record_count == 2
    assert [candidate.chunk.id for candidate in dense] == ["chunk-a", "chunk-b"]
    assert [candidate.chunk.id for candidate in sparse] == ["chunk-a"]
    assert dense[0].score > dense[1].score

    await search.delete_document_version("document-1", 1)
    assert search.record_count == 0
    await search.upsert_chunks([relevant, other])
    await search.delete_document("document-1")
    assert search.record_count == 0
