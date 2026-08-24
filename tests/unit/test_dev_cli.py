from __future__ import annotations

from pathlib import Path

import pytest

from rag_mvp.dev.cli import UPLOAD_FRAME_BYTES, _parser, _upload_requests


@pytest.mark.parametrize(
    "command,extra",
    [
        (
            "create-dataset",
            ["--name", "docs", "--embedding-model", "embed-v1", "--embedding-dimension", "3"],
        ),
        ("submit-document", ["--dataset-id", "dataset-1", "--file", "guide.md"]),
        ("retry-job", ["--job-id", "job-1"]),
        ("cancel-job", ["--job-id", "job-1"]),
        ("delete-document", ["--document-id", "document-1"]),
    ],
)
def test_mutating_commands_require_request_and_idempotency_keys(
    command: str,
    extra: list[str],
) -> None:
    parser = _parser()

    with pytest.raises(SystemExit):
        parser.parse_args([command, "--request-id", "request-1", *extra])

    arguments = parser.parse_args(
        [
            command,
            "--request-id",
            "request-1",
            "--idempotency-key",
            "idempotency-1",
            *extra,
        ]
    )
    assert arguments.command == command


@pytest.mark.asyncio
async def test_submit_document_streams_one_header_then_bounded_data_frames(
    tmp_path: Path,
) -> None:
    source = tmp_path / "guide.md"
    content = b"a" * (UPLOAD_FRAME_BYTES + 7)
    source.write_bytes(content)
    arguments = _parser().parse_args(
        [
            "submit-document",
            "--request-id",
            "request-1",
            "--idempotency-key",
            "idempotency-1",
            "--dataset-id",
            "dataset-1",
            "--file",
            str(source),
        ]
    )

    frames = [frame async for frame in _upload_requests(arguments)]

    assert [frame.WhichOneof("payload") for frame in frames] == ["header", "data", "data"]
    assert frames[0].header.source_name == "guide.md"
    assert frames[0].header.context.request_id == "request-1"
    assert b"".join(frame.data for frame in frames[1:]) == content
