"""RAG 执行边界的统一结构化事件字段，保证请求链路可关联。"""

from __future__ import annotations

import structlog

from rag_mvp.telemetry import current_ids

_LOGGER = structlog.get_logger("rag_mvp")


# 以统一关联字段写入一条 RAG 生命周期结构化日志事件。
def emit_event(
    event: str,
    *,
    request_id: str | None = None,
    job_id: str | None = None,
    document_id: str | None = None,
    dataset_id: str | None = None,
    stage: str,
    duration_ms: float = 0.0,
    index_version: int | None = None,
    error_code: str | None = None,
    failure_message: str | None = None,
    retry_in_seconds: float | None = None,
) -> None:
    """Emit one event with the complete correlation schema, including absent values."""

    trace_id, span_id = current_ids()
    _LOGGER.info(
        event,
        request_id=request_id,
        job_id=job_id,
        document_id=document_id,
        dataset_id=dataset_id,
        stage=stage,
        duration_ms=duration_ms,
        index_version=index_version,
        error_code=error_code,
        failure_message=failure_message,
        retry_in_seconds=retry_in_seconds,
        trace_id=trace_id,
        span_id=span_id,
    )
