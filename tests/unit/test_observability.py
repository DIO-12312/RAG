from __future__ import annotations

from structlog.testing import capture_logs

from rag_mvp.observability import emit_event


def test_rag_event_always_contains_correlation_and_stage_fields() -> None:
    with capture_logs() as logs:
        emit_event(
            "ingestion_completed",
            request_id="request-1",
            job_id="job-1",
            document_id="document-1",
            dataset_id="dataset-1",
            stage="complete",
            duration_ms=12.5,
            index_version=3,
            error_code=None,
        )

    assert len(logs) == 1
    assert logs[0] == {
        "event": "ingestion_completed",
        "log_level": "info",
        "request_id": "request-1",
        "job_id": "job-1",
        "document_id": "document-1",
        "dataset_id": "dataset-1",
        "stage": "complete",
        "duration_ms": 12.5,
        "index_version": 3,
        "error_code": None,
    }
