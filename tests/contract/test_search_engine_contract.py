from __future__ import annotations

# 校验搜索引擎端口的稠密、稀疏候选与版本过滤语义。
import pytest

from rag_mvp.domain.models import Chunk, Locator
from rag_mvp.ports.search_engine import (
    IndexedChunk,
    SearchRequest,
    TopicNeighborAnchor,
    TopicNeighborRequest,
    TopicReferenceAnchor,
    TopicReferenceRequest,
)
from tests.fakes.search_engine import FakeSearchEngine


def _indexed(
    content: str,
    chunk_id: str,
    vector: tuple[float, ...],
    *,
    dataset_id: str = "dataset-1",
    document_id: str = "document-1",
    ordinal: int = 0,
    topic_path: str | None = None,
) -> IndexedChunk:
    """构造本测试所需的输入、替身或运行环境。"""
    chunk = Chunk(
        id=chunk_id,
        document_id=document_id,
        index_version=1,
        ordinal=ordinal,
        content_with_weight=content,
        content_sha256="c" * 64,
        source_name="guide.txt",
        locator=Locator(start_line=1, end_line=1),
        metadata={
            "category": "guide",
            **({"source_type": "chm", "topic_path": topic_path} if topic_path else {}),
        },
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


@pytest.mark.asyncio
async def test_search_topic_neighbors_stay_inside_document_version_and_topic() -> None:
    search = FakeSearchEngine()
    chunks = [
        _indexed(
            f"topic-a-{ordinal}",
            f"chunk-{ordinal}",
            (1.0,),
            ordinal=ordinal,
            topic_path="topic-a.html",
        )
        for ordinal in range(3)
    ]
    chunks.extend(
        (
            _indexed(
                "other topic",
                "chunk-other-topic",
                (1.0,),
                ordinal=3,
                topic_path="topic-b.html",
            ),
            _indexed(
                "other document",
                "chunk-other-document",
                (1.0,),
                document_id="document-2",
                ordinal=1,
                topic_path="topic-a.html",
            ),
        )
    )
    await search.upsert_chunks(chunks)

    neighbors = await search.topic_neighbors(
        TopicNeighborRequest(
            dataset_id="dataset-1",
            anchors=(
                TopicNeighborAnchor(
                    document_id="document-1",
                    index_version=1,
                    ordinal=1,
                    topic_path="topic-a.html",
                ),
            ),
            radius=1,
            filters={"category": "guide"},
        )
    )

    assert [candidate.chunk.id for candidate in neighbors] == [
        "chunk-0",
        "chunk-1",
        "chunk-2",
    ]


@pytest.mark.asyncio
async def test_search_resolves_chi_topic_reference_inside_dataset_and_source() -> None:
    search = FakeSearchEngine()
    matching = _indexed(
        "DDS_DataReader_take reads samples",
        "chunk-match",
        (1.0,),
        topic_path="group___c_subscription.html",
    )
    wrong_topic = _indexed(
        "DDS_DataReader_take elsewhere",
        "chunk-wrong-topic",
        (1.0,),
        topic_path="other.html",
    )
    wrong_dataset = _indexed(
        "DDS_DataReader_take isolated",
        "chunk-wrong-dataset",
        (1.0,),
        dataset_id="dataset-2",
        document_id="document-2",
        topic_path="group___c_subscription.html",
    )
    await search.upsert_chunks((matching, wrong_topic, wrong_dataset))

    references = await search.topic_references(
        TopicReferenceRequest(
            dataset_id="dataset-1",
            query="DDS_DataReader_take",
            anchors=(
                TopicReferenceAnchor(
                    anchor_chunk_id="chi-chunk",
                    associated_source_name="guide.txt",
                    topic_path="group___c_subscription.html",
                ),
            ),
            filters={"category": "guide"},
        )
    )

    assert [candidate.chunk.id for candidate in references] == ["chunk-match"]
