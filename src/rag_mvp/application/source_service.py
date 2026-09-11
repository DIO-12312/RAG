"""只读来源服务：从权威原文件恢复一个完整、已清洗的 CHM Topic。"""

from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from rag_mvp.application.dto import GetSourceTopicQuery, SourceTopicView
from rag_mvp.domain.enums import DocumentStatus
from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.ports.metadata import MetadataRepository
from rag_mvp.ports.parser import ParsedSegment, Parser
from rag_mvp.ports.storage import ObjectStorage


class SourceService:
    """Read an active CHM source object without putting the full Topic into retrieval context."""

    def __init__(
        self,
        metadata: MetadataRepository,
        storage: ObjectStorage,
        parser: Parser,
    ) -> None:
        self._metadata = metadata
        self._storage = storage
        self._parser = parser

    async def get_topic(self, query: GetSourceTopicQuery) -> SourceTopicView:
        if not query.document_id.strip():
            raise ValueError("document_id must not be empty")
        if query.index_version < 1:
            raise ValueError("index_version must be at least 1")
        topic_path = _normalize_topic_path(query.topic_path)
        document = await self._metadata.get_document(query.document_id)
        if document is None or document.status is DocumentStatus.DELETED:
            raise DomainError(DomainFailure("DOCUMENT_NOT_FOUND", "document does not exist"))
        if (
            document.status is not DocumentStatus.READY
            or document.active_version is None
            or document.object_key is None
        ):
            raise DomainError(
                DomainFailure(
                    "DOCUMENT_NOT_READY",
                    "document source is not ready",
                    retryable=True,
                )
            )
        if document.active_version != query.index_version:
            raise DomainError(
                DomainFailure(
                    "SOURCE_VERSION_NOT_ACTIVE",
                    "citation no longer belongs to the active document version",
                )
            )
        if PurePosixPath(document.source_name).suffix.casefold() != ".chm":
            raise DomainError(
                DomainFailure(
                    "SOURCE_TOPIC_UNSUPPORTED",
                    "full Topic source is available only for CHM documents",
                )
            )

        content = await self._storage.read(document.object_key)
        segments = tuple(await self._parser.parse(document.source_name, content))
        topic_segments = tuple(
            segment
            for segment in segments
            if segment.metadata.get("source_type") == "chm"
            and segment.metadata.get("topic_path", "").casefold() == topic_path.casefold()
        )
        if not topic_segments:
            raise DomainError(DomainFailure("SOURCE_TOPIC_NOT_FOUND", "CHM Topic does not exist"))
        topic_title = (
            topic_segments[0].metadata.get("topic_title") or PurePosixPath(topic_path).stem
        )
        return SourceTopicView(
            document_id=document.id,
            source_name=document.source_name,
            topic_path=topic_path,
            topic_title=topic_title,
            markdown=_render_topic_markdown(topic_title, topic_segments),
            anchor=query.anchor,
        )


def _normalize_topic_path(value: str) -> str:
    path = unquote(urlsplit(value.strip().replace("\\", "/")).path).lstrip("/")
    parts = PurePosixPath(path).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("topic_path must be a safe relative path")
    normalized = PurePosixPath(*parts).as_posix()
    if PurePosixPath(normalized).suffix.casefold() not in {".htm", ".html", ".xhtm", ".xhtml"}:
        raise ValueError("topic_path must identify an HTML Topic")
    return normalized


def _render_topic_markdown(title: str, segments: tuple[ParsedSegment, ...]) -> str:
    lines = [f"# {title}"]
    for segment in segments:
        paragraphs = [part.strip() for part in segment.text.split("\n\n") if part.strip()]
        heading = segment.locator.symbol
        level_text = segment.metadata.get("heading_level")
        if heading and level_text and level_text.isdigit():
            if paragraphs and paragraphs[0] == heading:
                paragraphs.pop(0)
            level = min(6, max(1, int(level_text)))
            if heading != title or level != 1:
                lines.extend(("", f"{'#' * level} {heading}"))
        for paragraph in paragraphs:
            lines.extend(("", paragraph))
    return "\n".join(lines).strip()
