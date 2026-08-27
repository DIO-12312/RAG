"""RAG 执行边界的统一结构化事件字段，保证请求链路可关联。"""

from __future__ import annotations

import structlog

_LOGGER = structlog.get_logger("rag_mvp")


# 实现 emit_event 对应的局部职责。
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
) -> None:
    """Emit one event with the complete correlation schema, including absent values."""

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
    )
