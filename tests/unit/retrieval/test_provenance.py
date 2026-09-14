from __future__ import annotations

from rag_mvp.domain.ids import content_sha256
from rag_mvp.domain.models import Chunk, Locator
from rag_mvp.ports.search_engine import SearchCandidate
from rag_mvp.retrieval.provenance import dense_evidence


def test_dense_evidence_preserves_traceable_chunk_fields() -> None:
    """验证本测试场景的预期行为与边界条件。"""
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


def test_chm_evidence_exposes_body_without_retrieval_weight_prefix() -> None:
    weighted = (
        "Topic: DDS WaitSet\nHeading: DDS WaitSet > wait\nSymbol: DDS_WaitSet_wait\n\n"
        "Wait for an active condition."
    )
    chunk = Chunk(
        id="chunk-1",
        document_id="document-1",
        index_version=1,
        ordinal=0,
        content_with_weight=weighted,
        content_sha256=content_sha256(weighted),
        source_name="manual.chm",
        locator=Locator(symbol="DDS_WaitSet_wait"),
        metadata={"source_type": "chm", "topic_path": "waitset.html"},
    )

    evidence = dense_evidence(SearchCandidate("record-1", "dataset-1", chunk, 0.8))

    assert evidence.content_with_weight == weighted
    assert evidence.display_content == "Wait for an active condition."


def test_pdf_evidence_repairs_false_tables_and_emits_valid_markdown_tables() -> None:
    heading = "第5章实体 > 5.3.1 Listener"
    false_table = (
        f"{heading}\n\n"
        "| Listener 是所有实体的 | DomainParticipant、Topic、Publisher、 |\n"
        "| Subscriber、DataWriter、DataReader 都关联特定 Listener，这些 | Listener |\n"
        "| 不同类型实体提供不同方法。 | 状态变化时由 ZRDDS 调用。 | extra |\n"
        "| 在使用 Listener 时需要实现回调接口函数。 | 回调通知用户。 |"
    )
    chunk = Chunk(
        id="pdf-paragraph",
        document_id="document-1",
        index_version=1,
        ordinal=0,
        content_with_weight=false_table,
        content_sha256=content_sha256(false_table),
        source_name="manual.pdf",
        locator=Locator(page_number=40),
        metadata={"source_type": "pdf", "heading_path": heading, "layout_type": "table"},
    )
    evidence = dense_evidence(SearchCandidate("record-1", "dataset-1", chunk, 0.8))

    assert heading not in evidence.display_content
    assert not any(line.startswith("|") for line in evidence.display_content.splitlines())
    assert "Listener 是所有实体的 DomainParticipant" in evidence.display_content

    real_table = "| Listeners | Callback Functions |\n| Topic | on_inconsistent_topic() |"
    table_chunk = Chunk(
        id="pdf-table",
        document_id="document-1",
        index_version=1,
        ordinal=1,
        content_with_weight=real_table,
        content_sha256=content_sha256(real_table),
        source_name="manual.pdf",
        locator=Locator(page_number=40),
        metadata={"source_type": "pdf", "layout_type": "table"},
    )
    table = dense_evidence(SearchCandidate("record-2", "dataset-1", table_chunk, 0.7))
    assert "| --- | --- |" in table.display_content
