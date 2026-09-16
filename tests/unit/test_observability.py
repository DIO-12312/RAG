from __future__ import annotations

from structlog.testing import capture_logs

from rag_mvp.observability import emit_event


def test_rag_event_always_contains_correlation_and_stage_fields() -> None:
    """验证本测试场景的预期行为与边界条件。"""
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
            failure_message="provider throttled the request",
            retry_in_seconds=15.0,
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
        "failure_message": "provider throttled the request",
        "retry_in_seconds": 15.0,
    }


def test_rag_event_records_absent_optional_fields_explicitly() -> None:
    """可选字段缺省时也必须出现，便于日志按同一 schema 查询。"""

    with capture_logs() as logs:
        emit_event("delivery_skipped", stage="worker_ack_terminal")

    assert logs[0]["failure_message"] is None
    assert logs[0]["retry_in_seconds"] is None
    assert logs[0]["job_id"] is None
