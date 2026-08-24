"""Unit tests for the Elasticsearch mapping and document codec."""

from __future__ import annotations

import pytest

from rag_mvp.adapters.search_engine.mapping import (
    bulk_action,
    candidate_from_hit,
    index_definition,
    source_from_indexed_chunk,
)
from rag_mvp.domain.models import Chunk, Locator
from rag_mvp.ports.search_engine import IndexedChunk


def _indexed(vector: tuple[float, ...] = (1.0, 0.0, 0.0)) -> IndexedChunk:
    chunk = Chunk(
        id="0123456789abcdef",
        document_id="document-1",
        index_version=2,
        ordinal=3,
        content_with_weight="Python semantic retrieval guide",
        content_sha256="c" * 64,
        source_name="guide.md",
        locator=Locator(
            page_number=4,
            start_line=12,
            end_line=20,
            symbol="retrieve",
            language="python",
            metadata={"section": "search"},
        ),
        metadata={"category": "guide", "visibility": "public"},
    )
    return IndexedChunk(
        record_id="document-1:2:0123456789abcdef",
        dataset_id="dataset-1",
        chunk=chunk,
        vector=vector,
    )


def test_index_definition_fixes_dense_cosine_and_searchable_field_types() -> None:
    definition = index_definition(1024)
    mappings = definition["mappings"]
    properties = mappings["properties"]

    assert mappings["dynamic"] == "strict"
    assert properties["vector"] == {
        "type": "dense_vector",
        "dims": 1024,
        "index": True,
        "similarity": "cosine",
    }
    assert properties["record_id"]["type"] == "keyword"
    assert properties["dataset_id"]["type"] == "keyword"
    assert properties["document_id"]["type"] == "keyword"
    assert properties["chunk_id"]["type"] == "keyword"
    assert properties["content_with_weight"]["type"] == "text"
    assert properties["source_name"]["fields"]["text"]["type"] == "text"
    assert properties["metadata"]["type"] == "flattened"
    assert properties["locator"]["properties"]["metadata"]["type"] == "flattened"


def test_index_definition_rejects_non_positive_dimension() -> None:
    with pytest.raises(ValueError, match="dimension"):
        index_definition(0)


def test_indexed_chunk_round_trips_through_source_and_hit_without_score_loss() -> None:
    indexed = _indexed()
    source = source_from_indexed_chunk(indexed)
    candidate = candidate_from_hit({"_id": indexed.record_id, "_score": 0.875, "_source": source})

    assert source["vector"] == [1.0, 0.0, 0.0]
    assert candidate.record_id == indexed.record_id
    assert candidate.dataset_id == indexed.dataset_id
    assert candidate.chunk == indexed.chunk
    assert candidate.score == 0.875


def test_bulk_action_uses_versioned_physical_id_and_rejects_mismatch() -> None:
    indexed = _indexed()
    action = bulk_action("rag-chunks-v1", indexed, embedding_dimension=3)

    assert action["_op_type"] == "index"
    assert action["_index"] == "rag-chunks-v1"
    assert action["_id"] == "document-1:2:0123456789abcdef"

    mismatched = IndexedChunk(
        record_id="wrong-id",
        dataset_id=indexed.dataset_id,
        chunk=indexed.chunk,
        vector=indexed.vector,
    )
    with pytest.raises(ValueError, match="record_id"):
        bulk_action("rag-chunks-v1", mismatched, embedding_dimension=3)


def test_bulk_action_rejects_vector_dimension_mismatch() -> None:
    with pytest.raises(ValueError, match="dimension"):
        bulk_action("rag-chunks-v1", _indexed((1.0, 0.0)), embedding_dimension=3)
