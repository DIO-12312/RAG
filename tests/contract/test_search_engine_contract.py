from __future__ import annotations

# 校验搜索引擎端口的稠密、稀疏候选与版本过滤语义。
import pytest

from rag_mvp.domain.models import Chunk, Locator
from rag_mvp.ports.search_engine import IndexedChunk, SearchRequest
from tests.fakes.search_engine import FakeSearchEngine


def _indexed(
    content: str,
    chunk_id: str,
    vector: tuple[float, ...],
    *,
    dataset_id: str = "dataset-1",
    document_id: str = "document-1",
) -> IndexedChunk:
    """构造本测试所需的输入、替身或运行环境。"""
    chunk = Chunk(
        id=chunk_id,
        document_id=document_id,
        index_version=1,
        ordinal=0,
        content_with_weight=content,
        content_sha256="c" * 64,
        source_name="guide.txt",
        locator=Locator(start_line=1, end_line=1),
        metadata={"category": "guide"},
    )
    return IndexedChunk(
        record_id=f"{document_id}:1:{chunk_id}",
        dataset_id=dataset_id,
        chunk=chunk,
        vector=vector,
    )


@pytest.mark.asyncio
async def test_search_upsert_is_idempotent_and_dense_sparse_are_separate() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    search = FakeSearchEngine()
    relevant = _indexed("python retrieval guide", "chunk-a", (1.0, 0.0))
    other = _indexed("cooking notes", "chunk-b", (0.0, 1.0))
    isolated = _indexed(
        "python retrieval isolated",
        "chunk-c",
        (1.0, 0.0),
        dataset_id="dataset-2",
        document_id="document-2",
    )
    await search.upsert_chunks([relevant, other, isolated, relevant])

    dense = await search.dense_search(
        SearchRequest(
            dataset_id="dataset-1",
            top_k=2,
            query_vector=(1.0, 0.0),
            filters={"category": "guide"},
        )
    )
    sparse = await search.sparse_search(
        SearchRequest(
            dataset_id="dataset-1",
            top_k=2,
            query="python guide",
            filters={"category": "guide"},
        )
    )

    assert search.record_count == 3
    assert [candidate.chunk.id for candidate in dense] == ["chunk-a", "chunk-b"]
    assert [candidate.chunk.id for candidate in sparse] == ["chunk-a"]
    assert dense[0].score > dense[1].score

    await search.delete_document_version("document-1", 1)
    assert search.record_count == 1
    await search.upsert_chunks([relevant, other])
    await search.delete_document("document-1")
    assert search.record_count == 1
    await search.delete_document("document-2")
    assert search.record_count == 0


@pytest.mark.asyncio
async def test_search_can_delete_an_entire_dataset_idempotently() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    search = FakeSearchEngine()
    await search.upsert_chunks(
        [
            _indexed("one", "chunk-1", (1.0,), dataset_id="dataset-1"),
            _indexed("two", "chunk-2", (1.0,), dataset_id="dataset-2", document_id="document-2"),
        ]
    )

    await search.delete_dataset("dataset-1")
    await search.delete_dataset("dataset-1")

    assert search.record_count == 1
