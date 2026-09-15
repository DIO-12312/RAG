"""将搜索候选纯转换为可追溯 evidence；最终引用编号由未来 Go 层生成。"""

from __future__ import annotations

from collections.abc import Mapping
from statistics import median

from rag_mvp.domain.models import Evidence, ScoreBreakdown
from rag_mvp.ports.search_engine import SearchCandidate
from rag_mvp.retrieval.hybrid import HybridCandidate
from rag_mvp.retrieval.rerank import RerankedCandidate


# 执行稠密检索该方法负责的领域数据或基础设施状态。
def dense_evidence(candidate: SearchCandidate) -> Evidence:
    chunk = candidate.chunk
    return Evidence(
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        content_with_weight=chunk.content_with_weight,
        source_name=chunk.source_name,
        locator=chunk.locator,
        metadata=chunk.metadata,
        display_content=_display_content(chunk.content_with_weight, chunk.metadata),
        scores=ScoreBreakdown(dense_score=candidate.score),
        index_version=chunk.index_version,
    )


# 实现 hybrid_evidence 对应的局部职责。
def hybrid_evidence(candidate: HybridCandidate) -> Evidence:
    chunk = candidate.chunk
    return Evidence(
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        content_with_weight=chunk.content_with_weight,
        source_name=chunk.source_name,
        locator=chunk.locator,
        metadata=chunk.metadata,
        display_content=_display_content(chunk.content_with_weight, chunk.metadata),
        scores=ScoreBreakdown(
            dense_score=candidate.dense_score,
            sparse_score=candidate.sparse_score,
            fusion_score=candidate.fusion_score,
        ),
        index_version=chunk.index_version,
    )


# 实现 reranked_evidence 对应的局部职责。
def reranked_evidence(candidate: RerankedCandidate) -> Evidence:
    chunk = candidate.chunk
    return Evidence(
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        content_with_weight=chunk.content_with_weight,
        source_name=chunk.source_name,
        locator=chunk.locator,
        metadata=chunk.metadata,
        display_content=_display_content(chunk.content_with_weight, chunk.metadata),
        scores=ScoreBreakdown(
            dense_score=candidate.dense_score,
            sparse_score=candidate.sparse_score,
            fusion_score=candidate.fusion_score,
            rerank_score=candidate.rerank_score,
        ),
        index_version=chunk.index_version,
    )


def _display_content(content: str, metadata: Mapping[str, str]) -> str:
    """Build the retrieval-layer citation projection without changing indexed text."""

    if metadata.get("source_type") != "pdf":
        return ""
    return _pdf_display_content(content, metadata.get("heading_path", ""))


def _pdf_display_content(content: str, heading_path: str) -> str:
    """Repair presentation-only artifacts from positioned PDF text fragments."""

    lines = content.splitlines()
    first_content = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first_content is not None and heading_path:
        normalized_line = " ".join(lines[first_content].split()).casefold()
        normalized_heading = " ".join(heading_path.split()).casefold()
        if normalized_line == normalized_heading:
            del lines[first_content]
            if first_content < len(lines) and not lines[first_content].strip():
                del lines[first_content]

    output: list[str] = []
    index = 0
    while index < len(lines):
        cells = _pdf_pipe_cells(lines[index])
        if cells is None:
            output.append(lines[index])
            index += 1
            continue
        rows: list[list[str]] = []
        while index < len(lines):
            row = _pdf_pipe_cells(lines[index])
            if row is None:
                break
            rows.append(row)
            index += 1
        if _looks_like_pdf_table(rows):
            width = max(len(row) for row in rows)
            padded = [row + [""] * (width - len(row)) for row in rows]
            output.append("| " + " | ".join(padded[0]) + " |")
            output.append("| " + " | ".join("---" for _ in range(width)) + " |")
            output.extend("| " + " | ".join(row) + " |" for row in padded[1:])
        else:
            output.extend(" ".join(cell for cell in row if cell).strip() for row in rows)
    return "\n".join(output).strip() or content


def _pdf_pipe_cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if len(stripped) < 2 or not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    cells = [cell.strip() for cell in stripped[1:-1].split("|")]
    return cells if any(cells) else None


def _looks_like_pdf_table(rows: list[list[str]]) -> bool:
    if len(rows) < 2:
        return False
    counts = [len(row) for row in rows]
    cells = [cell for row in rows for cell in row if cell]
    if min(counts) < 2 or max(counts) - min(counts) > 1 or not cells:
        return False
    if max(len(cell) for cell in cells) > 80 or median(len(cell) for cell in cells) > 32:
        return False
    prose_punctuation = sum(cell.count("。") + cell.count("；") for cell in cells)
    return prose_punctuation < len(rows)
