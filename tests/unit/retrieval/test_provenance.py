from __future__ import annotations

from rag_mvp.domain.ids import content_sha256
from rag_mvp.domain.models import Chunk, Locator
from rag_mvp.ports.search_engine import SearchCandidate
from rag_mvp.retrieval.provenance import dense_evidence


def test_dense_evidence_preserves_traceable_chunk_fields() -> None:
    chunk = Chunk(
        id="chunk-1",
        document_id="document-1",
        index_version=3,
        ordinal=0,
        content_with_weight="retrieval evidence",
        content_sha256=content_sha256("retrieval evidence"),
        source_name="guide.txt",
        locator=Locator(start_line=7, end_line=8, metadata={"section": "intro"}),
        metadata={"team": "search"},
    )
    candidate = SearchCandidate("record-1", "dataset-1", chunk, 0.75)

    evidence = dense_evidence(candidate)

    assert evidence.chunk_id == "chunk-1"
    assert evidence.document_id == "document-1"
    assert evidence.index_version == 3
    assert evidence.locator.start_line == 7
    assert evidence.metadata == {"team": "search"}
    assert evidence.scores.dense_score == 0.75
    assert evidence.scores.sparse_score is None
