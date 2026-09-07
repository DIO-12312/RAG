from __future__ import annotations

# 验证检索服务复核 active version 后执行融合、重排与 evidence 返回。
from datetime import UTC, datetime

import pytest

from rag_mvp.application.dto import RetrieveQuery
from rag_mvp.application.retrieval_service import RetrievalService
from rag_mvp.domain.enums import DocumentStatus
from rag_mvp.domain.errors import DomainError
from rag_mvp.domain.ids import content_sha256
from rag_mvp.domain.models import Chunk, Dataset, Document, Locator
from rag_mvp.ports.search_engine import IndexedChunk
from rag_mvp.retrieval.hybrid import HybridCandidate
from tests.fakes.metadata import FakeMetadataRepository
from tests.fakes.model import FakeModelGateway
from tests.fakes.search_engine import FakeSearchEngine


class FailingRerankModel(FakeModelGateway):
    async def rerank(self, query: str, passages: list[str]) -> list[float]:
        """模拟重排模型并返回可预测的分数。"""
        del query, passages
        raise ConnectionError("reranker unavailable")


def _chunk(
    document_id: str,
    version: int,
    content: str,
    *,
    team: str = "search",
    chunk_id: str | None = None,
    ordinal: int = 0,
    topic_path: str | None = None,
    source_type: str | None = None,
    source_name: str | None = None,
    locator_anchor: str | None = None,
    metadata_extra: dict[str, str] | None = None,
) -> Chunk:
    """构造本测试所需的输入、替身或运行环境。"""
    return Chunk(
        id=chunk_id or f"chunk-{version}",
        document_id=document_id,
        index_version=version,
        ordinal=ordinal,
        content_with_weight=content,
        content_sha256=content_sha256(content),
        source_name=source_name or ("manual.chm" if topic_path else "guide.txt"),
        locator=Locator(
            start_line=version,
            end_line=version,
            metadata={"anchor": locator_anchor} if locator_anchor else {},
        ),
        metadata={
            "team": team,
            **(
                {
                    "source_type": source_type or "chm",
                    "logical_document_type": "chm_topic",
                    "topic_path": topic_path,
                    "topic_title": "Topic A",
                    "heading_path": "Topic A > Details",
                }
                if topic_path
                else {}
            ),
            **(metadata_extra or {}),
        },
    )


@pytest.mark.asyncio
async def test_dense_retrieve_filters_stale_versions_and_preserves_scores() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    model = FakeModelGateway(8)
    search = FakeSearchEngine()
    await repository.create_dataset(Dataset("dataset-1", "Docs", "fake", 8, now))
    repository.documents["document-1"] = Document(
        id="document-1",
        dataset_id="dataset-1",
        source_name="guide.txt",
        file_sha256="0" * 64,
        status=DocumentStatus.READY,
        active_version=2,
        next_index_version=3,
        lifecycle_generation=0,
        created_at=now,
        object_key="objects/document-1/source",
    )
    vector = (await model.embed(["retrieval"]))[0]
    stale = _chunk("document-1", 1, "stale retrieval")
    active = _chunk("document-1", 2, "active retrieval")
    await search.upsert_chunks(
        (
            IndexedChunk("stale-record", "dataset-1", stale, vector),
            IndexedChunk("active-record", "dataset-1", active, vector),
        )
    )
    service = RetrievalService(repository, search, model)

    result = await service.retrieve(
        RetrieveQuery(
            request_id="request-1",
            dataset_id="dataset-1",
            query="retrieval",
            top_k=5,
            filters={"team": "search"},
            max_context_tokens=100,
        )
    )

    assert [item.content_with_weight for item in result.evidence] == ["active retrieval"]
    assert result.evidence[0].scores.dense_score == pytest.approx(1.0)
    assert result.evidence[0].scores.sparse_score is not None
    assert result.evidence[0].scores.fusion_score is not None
    assert result.evidence[0].index_version == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "dataset_id", "enable_rerank", "code"),
    [
        ("", "dataset-1", False, "QUERY_REQUIRED"),
        ("query", "missing", False, "DATASET_NOT_FOUND"),
    ],
)
async def test_retrieve_rejects_invalid_or_unavailable_requests(
    query: str, dataset_id: str, enable_rerank: bool, code: str
) -> None:
    """验证本测试场景的预期行为与边界条件。"""
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    await repository.create_dataset(Dataset("dataset-1", "Docs", "fake", 8, now))
    service = RetrievalService(repository, FakeSearchEngine(), FakeModelGateway(8))

    with pytest.raises(DomainError) as error:
        await service.retrieve(
            RetrieveQuery(
                "request-1",
                dataset_id,
                query,
                5,
                {},
                100,
                enable_rerank,
            )
        )

    assert error.value.failure.code == code


@pytest.mark.asyncio
async def test_rerank_failure_degrades_to_rrf_evidence() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    model = FailingRerankModel(8)
    search = FakeSearchEngine()
    await repository.create_dataset(Dataset("dataset-1", "Docs", "fake", 8, now))
    repository.documents["document-1"] = Document(
        id="document-1",
        dataset_id="dataset-1",
        source_name="guide.txt",
        file_sha256="0" * 64,
        status=DocumentStatus.READY,
        active_version=1,
        next_index_version=2,
        lifecycle_generation=0,
        created_at=now,
        object_key="objects/document-1/source",
    )
    chunk = _chunk("document-1", 1, "active retrieval")
    vector = (await model.embed(["retrieval"]))[0]
    await search.upsert_chunks((IndexedChunk("record-1", "dataset-1", chunk, vector),))

    result = await RetrievalService(repository, search, model).retrieve(
        RetrieveQuery("request", "dataset-1", "retrieval", 5, {}, 100, True)
    )

    assert [item.chunk_id for item in result.evidence] == [chunk.id]
    assert result.evidence[0].scores.fusion_score is not None
    assert result.evidence[0].scores.rerank_score is None


@pytest.mark.asyncio
async def test_chm_retrieve_appends_same_topic_neighbors_before_context_budget() -> None:
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    model = FakeModelGateway(8)
    search = FakeSearchEngine()
    await repository.create_dataset(Dataset("dataset-1", "Docs", "fake", 8, now))
    repository.documents["document-1"] = Document(
        id="document-1",
        dataset_id="dataset-1",
        source_name="manual.chm",
        file_sha256="0" * 64,
        status=DocumentStatus.READY,
        active_version=1,
        next_index_version=2,
        lifecycle_generation=0,
        created_at=now,
        object_key="objects/document-1/source",
    )
    vector = (await model.embed(["anchor query"]))[0]
    before = _chunk(
        "document-1",
        1,
        "before context",
        chunk_id="chunk-before",
        ordinal=0,
        topic_path="topic-a.html",
    )
    anchor = _chunk(
        "document-1",
        1,
        "anchor query",
        chunk_id="chunk-anchor",
        ordinal=1,
        topic_path="topic-a.html",
    )
    after = _chunk(
        "document-1",
        1,
        "after exact symbol",
        chunk_id="chunk-after",
        ordinal=2,
        topic_path="topic-a.html",
    )
    other_topic = _chunk(
        "document-1",
        1,
        "other topic",
        chunk_id="chunk-other",
        ordinal=2,
        topic_path="topic-b.html",
    )
    await search.upsert_chunks(
        (
            IndexedChunk("z-before", "dataset-1", before, vector),
            IndexedChunk("a-anchor", "dataset-1", anchor, vector),
            IndexedChunk("z-after", "dataset-1", after, vector),
            IndexedChunk("z-other", "dataset-1", other_topic, vector),
        )
    )

    result = await RetrievalService(repository, search, model).retrieve(
        RetrieveQuery("request", "dataset-1", "anchor query", 1, {}, 100)
    )

    assert [item.chunk_id for item in result.evidence] == [
        "chunk-anchor",
        "chunk-before",
        "chunk-after",
    ]
    assert all(item.metadata["topic_path"] == "topic-a.html" for item in result.evidence)
    assert result.evidence[1].metadata["retrieval_role"] == "topic_neighbor"
    assert result.evidence[1].metadata["anchor_chunk_id"] == "chunk-anchor"
    assert result.evidence[1].scores.fusion_score is None


@pytest.mark.asyncio
async def test_chi_hit_resolves_associated_chm_topic_and_then_expands_neighbors() -> None:
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    model = FakeModelGateway(8)
    search = FakeSearchEngine()
    await repository.create_dataset(Dataset("dataset-1", "Docs", "fake", 8, now))
    for document_id, source_name in (
        ("chi-document", "manual.chi"),
        ("chm-document", "manual.chm"),
    ):
        repository.documents[document_id] = Document(
            id=document_id,
            dataset_id="dataset-1",
            source_name=source_name,
            file_sha256="0" * 64,
            status=DocumentStatus.READY,
            active_version=1,
            next_index_version=2,
            lifecycle_generation=0,
            created_at=now,
            object_key=f"objects/{document_id}/source",
        )

    query = "DDS_DataReader_take"
    vector = (await model.embed([query]))[0]
    chi = _chunk(
        "chi-document",
        1,
        query,
        chunk_id="chi-anchor",
        source_type="chi",
        source_name="manual.chi",
        topic_path="group___c_subscription.html",
        metadata_extra={
            "logical_document_type": "chm_index",
            "associated_chm_source_name": "manual.chm",
            "topic_url": "group___c_subscription.html#ga-take",
            "anchor": "ga-take",
        },
    )
    before = _chunk(
        "chm-document",
        1,
        "DataReader operations",
        chunk_id="chm-before",
        ordinal=0,
        topic_path="group___c_subscription.html",
    )
    target = _chunk(
        "chm-document",
        1,
        f"{query} reads available samples",
        chunk_id="chm-target",
        ordinal=1,
        topic_path="group___c_subscription.html",
        locator_anchor="ga-take",
    )
    await search.upsert_chunks(
        (
            IndexedChunk("a-chi", "dataset-1", chi, vector),
            IndexedChunk("z-before", "dataset-1", before, vector),
            IndexedChunk("z-target", "dataset-1", target, vector),
        )
    )

    result = await RetrievalService(repository, search, model).retrieve(
        RetrieveQuery("request", "dataset-1", query, 1, {}, 200)
    )

    assert [item.chunk_id for item in result.evidence[:2]] == ["chi-anchor", "chm-target"]
    assert result.evidence[1].metadata["retrieval_role"] == "chi_topic_reference"
    assert result.evidence[1].metadata["anchor_chunk_id"] == "chi-anchor"
    assert result.evidence[1].metadata["chi_topic_url"].endswith("#ga-take")
    assert result.evidence[1].scores.fusion_score is None
    assert result.evidence[2].chunk_id == "chm-before"
    assert result.evidence[2].metadata["retrieval_role"] == "topic_neighbor"


@pytest.mark.asyncio
async def test_chi_reference_replaces_duplicate_direct_chm_anchor() -> None:
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    model = FakeModelGateway(8)
    search = FakeSearchEngine()
    await repository.create_dataset(Dataset("dataset-1", "Docs", "fake", 8, now))
    for document_id, source_name in (
        ("chi-document", "manual.chi"),
        ("chm-document", "manual.chm"),
    ):
        repository.documents[document_id] = Document(
            id=document_id,
            dataset_id="dataset-1",
            source_name=source_name,
            file_sha256="0" * 64,
            status=DocumentStatus.READY,
            active_version=1,
            next_index_version=2,
            lifecycle_generation=0,
            created_at=now,
            object_key=f"objects/{document_id}/source",
        )

    query = "DDS_WaitSet_wait"
    vector = (await model.embed([query]))[0]
    chi = _chunk(
        "chi-document",
        1,
        query,
        chunk_id="chi-anchor",
        source_type="chi",
        source_name="manual.chi",
        topic_path="group___c_infrastruct.html",
        metadata_extra={
            "logical_document_type": "chm_index",
            "associated_chm_source_name": "manual.chm",
            "topic_url": "group___c_infrastruct.html#ga-wait",
            "anchor": "ga-wait",
        },
    )
    target = _chunk(
        "chm-document",
        1,
        f"{query} waits for active conditions",
        chunk_id="chm-target",
        topic_path="group___c_infrastruct.html",
        locator_anchor="ga-wait",
    )
    await search.upsert_chunks(
        (
            IndexedChunk("a-chi", "dataset-1", chi, vector),
            IndexedChunk("b-target", "dataset-1", target, vector),
        )
    )

    result = await RetrievalService(repository, search, model).retrieve(
        RetrieveQuery("request", "dataset-1", query, 2, {}, 200)
    )

    assert [item.chunk_id for item in result.evidence] == ["chi-anchor", "chm-target"]
    assert "retrieval_role" not in result.evidence[1].metadata
    assert result.evidence[1].metadata["chi_reference_anchor_chunk_id"] == "chi-anchor"
    assert result.evidence[1].metadata["chi_topic_url"].endswith("#ga-wait")
    assert result.evidence[1].scores.fusion_score is not None


def test_identifier_priority_supports_mixed_case_c_api_names() -> None:
    query = "DDS_DomainParticipantFactory_create_participant"
    lower_score_exact = _chunk(
        "document-1",
        1,
        query,
        chunk_id="exact",
    )
    higher_score_unrelated = _chunk(
        "document-2",
        1,
        "general participant documentation",
        chunk_id="unrelated",
    )
    ranked = RetrievalService._prioritize_identifiers(
        query,
        (
            HybridCandidate("unrelated", "dataset-1", higher_score_unrelated, None, None, 1.0),
            HybridCandidate("exact", "dataset-1", lower_score_exact, None, None, 0.1),
        ),
    )

    assert [candidate.record_id for candidate in ranked] == ["exact", "unrelated"]
