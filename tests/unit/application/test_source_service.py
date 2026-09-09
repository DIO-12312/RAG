from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rag_mvp.application.dto import GetSourceTopicQuery
from rag_mvp.application.source_service import SourceService
from rag_mvp.domain.enums import DocumentStatus
from rag_mvp.domain.errors import DomainError
from rag_mvp.domain.models import Document, Locator
from rag_mvp.ports.parser import ParsedSegment


class _Metadata:
    def __init__(self, document: Document | None) -> None:
        self.document = document

    async def get_document(self, document_id: str) -> Document | None:
        assert document_id == "document-1"
        return self.document


class _Storage:
    async def read(self, key: str) -> bytes:
        assert key == "objects/document-1/source"
        return b"fake-chm"


class _Parser:
    async def parse(self, source_name: str, content: bytes) -> tuple[ParsedSegment, ...]:
        assert source_name == "manual.chm"
        assert content == b"fake-chm"
        shared = {
            "source_type": "chm",
            "topic_path": "api/waitset.html",
            "topic_title": "DDS WaitSet",
        }
        return (
            ParsedSegment(
                "DDS_WaitSet_wait\n\nWait for an active condition.",
                Locator(symbol="DDS_WaitSet_wait", metadata={"anchor": "wait"}),
                {**shared, "heading_level": "1"},
            ),
            ParsedSegment(
                "Parameters\n\ntimeout specifies the maximum wait duration.",
                Locator(symbol="Parameters"),
                {**shared, "heading_level": "2"},
            ),
        )


def _document(*, version: int = 2, status: DocumentStatus = DocumentStatus.READY) -> Document:
    return Document(
        id="document-1",
        dataset_id="dataset-1",
        source_name="manual.chm",
        file_sha256="a" * 64,
        status=status,
        active_version=version,
        next_index_version=version + 1,
        lifecycle_generation=0,
        created_at=datetime.now(UTC),
        object_key="objects/document-1/source",
    )


@pytest.mark.asyncio
async def test_source_service_returns_complete_normalized_topic_as_markdown() -> None:
    service = SourceService(_Metadata(_document()), _Storage(), _Parser())  # type: ignore[arg-type]

    result = await service.get_topic(
        GetSourceTopicQuery(
            request_id="request-1",
            document_id="document-1",
            index_version=2,
            topic_path="/api/waitset.html#wait",
            anchor="wait",
        )
    )

    assert result.topic_title == "DDS WaitSet"
    assert result.topic_path == "api/waitset.html"
    assert result.anchor == "wait"
    assert result.markdown.startswith("# DDS WaitSet")
    assert "DDS_WaitSet_wait" in result.markdown
    assert "## Parameters" in result.markdown
    assert "timeout specifies" in result.markdown


@pytest.mark.asyncio
async def test_source_service_rejects_stale_citation_version() -> None:
    service = SourceService(_Metadata(_document()), _Storage(), _Parser())  # type: ignore[arg-type]

    with pytest.raises(DomainError) as error:
        await service.get_topic(
            GetSourceTopicQuery("request-1", "document-1", 1, "api/waitset.html")
        )

    assert error.value.failure.code == "SOURCE_VERSION_NOT_ACTIVE"


@pytest.mark.asyncio
async def test_source_service_rejects_unsafe_topic_path() -> None:
    service = SourceService(_Metadata(_document()), _Storage(), _Parser())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="safe relative path"):
        await service.get_topic(GetSourceTopicQuery("request-1", "document-1", 2, "../secret.html"))
