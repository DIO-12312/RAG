from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_mvp.adapters.chunkers.recursive import RecursiveChunker
from rag_mvp.domain.models import Locator
from rag_mvp.ports.parser import ParsedSegment


@pytest.mark.asyncio
async def test_recursive_chunker_is_stable_bounded_and_overlapping() -> None:
    chunker = RecursiveChunker(chunk_size=12, overlap=4)
    segments = (
        ParsedSegment(
            text="alpha beta\ngamma delta\nepsilon",
            locator=Locator(start_line=10, end_line=12),
            metadata={"section": "intro"},
        ),
    )

    first = await chunker.split(segments)
    second = await chunker.split(segments)

    assert first == second
    assert [chunk.ordinal for chunk in first] == list(range(len(first)))
    assert all(1 <= len(chunk.content_with_weight) <= 12 for chunk in first)
    assert first[0].locator.start_line == 10
    assert first[-1].locator.end_line == 12
    assert all(chunk.metadata["section"] == "intro" for chunk in first)
    assert set(first[0].content_with_weight[-4:]) & set(first[1].content_with_weight[:4])


def test_recursive_chunker_rejects_invalid_overlap() -> None:
    with pytest.raises(ValueError, match="overlap"):
        RecursiveChunker(chunk_size=10, overlap=10)


@pytest.mark.asyncio
async def test_recursive_chunker_matches_txt_golden_fixture() -> None:
    fixture_path = Path(__file__).parents[2] / "fixtures" / "golden_chunks" / "txt.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    chunker = RecursiveChunker(fixture["chunk_size"], fixture["overlap"])

    actual = await chunker.split(
        (
            ParsedSegment(
                text=fixture["input"],
                locator=Locator(start_line=fixture["start_line"], end_line=12),
            ),
        )
    )

    assert [
        {
            "ordinal": draft.ordinal,
            "content": draft.content_with_weight,
            "start_line": draft.locator.start_line,
            "end_line": draft.locator.end_line,
        }
        for draft in actual
    ] == fixture["chunks"]
