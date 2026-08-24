from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rag_mvp.application.dto import RetrieveQuery
from rag_mvp.application.retrieval_service import RetrievalService
from rag_mvp.domain.enums import DocumentStatus
from rag_mvp.domain.errors import DomainError
from rag_mvp.domain.ids import content_sha256
from rag_mvp.domain.models import Chunk, Dataset, Document, Locator
from rag_mvp.ports.search_engine import IndexedChunk
from tests.fakes.metadata import FakeMetadataRepository
from tests.fakes.model import FakeModelGateway
from tests.fakes.search_engine import FakeSearchEngine


def _chunk(document_id: str, version: int, content: str, *, team: str = "search") -> Chunk:
    return Chunk(
        id=f"chunk-{version}",
        document_id=document_id,
        index_version=version,
        ordinal=0,
        content_with_weight=content,
        content_sha256=content_sha256(content),
        source_name="guide.txt",
        locator=Locator(start_line=version, end_line=version),
        metadata={"team": team},
    )


@pytest.mark.asyncio
async def test_dense_retrieve_filters_stale_versions_and_preserves_scores() -> None:
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
        ("query", "dataset-1", True, "FEATURE_NOT_AVAILABLE"),
    ],
)
async def test_retrieve_rejects_invalid_or_unavailable_requests(
    query: str, dataset_id: str, enable_rerank: bool, code: str
) -> None:
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
