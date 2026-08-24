"""Elasticsearch index definition and domain document codec."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.domain.ids import es_record_id
from rag_mvp.domain.models import Chunk, Locator
from rag_mvp.ports.search_engine import IndexedChunk, SearchCandidate


def index_definition(embedding_dimension: int) -> dict[str, Any]:
    """Return the strict mapping shared by provisioning and schema validation."""

    if embedding_dimension < 1:
        raise ValueError("embedding_dimension must be at least 1")
    return {
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "record_id": {"type": "keyword"},
                "dataset_id": {"type": "keyword"},
                "document_id": {"type": "keyword"},
                "chunk_id": {"type": "keyword"},
                "index_version": {"type": "integer"},
                "ordinal": {"type": "integer"},
                "content_with_weight": {"type": "text"},
                "content_sha256": {"type": "keyword"},
                "vector": {
                    "type": "dense_vector",
                    "dims": embedding_dimension,
                    "index": True,
                    "similarity": "cosine",
                },
                "source_name": {
                    "type": "keyword",
                    "fields": {"text": {"type": "text"}},
                },
                "locator": {
                    "type": "object",
                    "dynamic": "strict",
                    "properties": {
                        "page_number": {"type": "integer"},
                        "start_line": {"type": "integer"},
                        "end_line": {"type": "integer"},
                        "symbol": {"type": "keyword"},
                        "language": {"type": "keyword"},
                        "metadata": {"type": "flattened"},
                    },
                },
                "metadata": {"type": "flattened"},
            },
        }
    }


def source_from_indexed_chunk(indexed: IndexedChunk) -> dict[str, Any]:
    """Encode one versioned chunk as an Elasticsearch `_source`."""

    chunk = indexed.chunk
    return {
        "record_id": indexed.record_id,
        "dataset_id": indexed.dataset_id,
        "document_id": chunk.document_id,
        "chunk_id": chunk.id,
        "index_version": chunk.index_version,
        "ordinal": chunk.ordinal,
        "content_with_weight": chunk.content_with_weight,
        "content_sha256": chunk.content_sha256,
        "vector": list(indexed.vector),
        "source_name": chunk.source_name,
        "locator": {
            "page_number": chunk.locator.page_number,
            "start_line": chunk.locator.start_line,
            "end_line": chunk.locator.end_line,
            "symbol": chunk.locator.symbol,
            "language": chunk.locator.language,
            "metadata": dict(chunk.locator.metadata),
        },
        "metadata": dict(chunk.metadata),
    }


def bulk_action(
    index_name: str,
    indexed: IndexedChunk,
    embedding_dimension: int,
) -> dict[str, Any]:
    """Build one idempotent Bulk `index` action with a versioned physical ID."""

    expected_record_id = es_record_id(
        indexed.chunk.document_id,
        indexed.chunk.index_version,
        indexed.chunk.id,
    )
    if indexed.record_id != expected_record_id:
        raise ValueError("record_id must match document, index version, and chunk ID")
    if len(indexed.vector) != embedding_dimension:
        raise ValueError("vector dimension must match the Elasticsearch mapping")
    if any(not math.isfinite(value) for value in indexed.vector):
        raise ValueError("vector values must be finite")
    return {
        "_op_type": "index",
        "_index": index_name,
        "_id": indexed.record_id,
        "_source": source_from_indexed_chunk(indexed),
    }


def candidate_from_hit(hit: Mapping[str, Any]) -> SearchCandidate:
    """Decode an Elasticsearch hit without altering its raw route score."""

    try:
        record_id = _required_text(hit, "_id")
        score = float(hit["_score"])
        source = _required_mapping(hit, "_source")
        locator_source = _required_mapping(source, "locator")
        chunk = Chunk(
            id=_required_text(source, "chunk_id"),
            document_id=_required_text(source, "document_id"),
            index_version=_required_int(source, "index_version"),
            ordinal=_required_int(source, "ordinal"),
            content_with_weight=_required_text(source, "content_with_weight"),
            content_sha256=_required_text(source, "content_sha256"),
            source_name=_required_text(source, "source_name"),
            locator=Locator(
                page_number=_optional_int(locator_source, "page_number"),
                start_line=_optional_int(locator_source, "start_line"),
                end_line=_optional_int(locator_source, "end_line"),
                symbol=_optional_text(locator_source, "symbol"),
                language=_optional_text(locator_source, "language"),
                metadata=_string_mapping(locator_source.get("metadata", {})),
            ),
            metadata=_string_mapping(source.get("metadata", {})),
        )
        return SearchCandidate(
            record_id=record_id,
            dataset_id=_required_text(source, "dataset_id"),
            chunk=chunk,
            score=score,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DomainError(
            DomainFailure(
                "SEARCH_RESPONSE_INVALID",
                "search engine returned an invalid hit",
                retryable=False,
            )
        ) from exc


def _required_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    nested = value[key]
    if not isinstance(nested, Mapping):
        raise TypeError(f"{key} must be an object")
    return nested


def _required_text(value: Mapping[str, Any], key: str) -> str:
    text = value[key]
    if not isinstance(text, str) or not text:
        raise TypeError(f"{key} must be non-empty text")
    return text


def _optional_text(value: Mapping[str, Any], key: str) -> str | None:
    text = value.get(key)
    if text is None:
        return None
    if not isinstance(text, str):
        raise TypeError(f"{key} must be text or null")
    return text


def _required_int(value: Mapping[str, Any], key: str) -> int:
    integer = value[key]
    if not isinstance(integer, int) or isinstance(integer, bool):
        raise TypeError(f"{key} must be an integer")
    return integer


def _optional_int(value: Mapping[str, Any], key: str) -> int | None:
    integer = value.get(key)
    if integer is None:
        return None
    if not isinstance(integer, int) or isinstance(integer, bool):
        raise TypeError(f"{key} must be an integer or null")
    return integer


def _string_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError("metadata must be an object")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise TypeError("metadata keys and values must be strings")
        result[key] = item
    return result
