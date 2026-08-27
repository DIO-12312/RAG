"""先提升 staging 对象，再使其 Outbox 事件可发布，保证任务读到正式源对象。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime

from rag_mvp.ports.metadata import MetadataRepository
from rag_mvp.ports.storage import ObjectStorage


# 关键语义：只有对象提升成功并完成 MySQL 条件更新后，Relay 才能看见 READY 事件；
# 若条件更新失败，必须删除刚提升的对象，避免删除/取消竞态遗留孤儿文件。
async def finalize_once(
    metadata: MetadataRepository,
    storage: ObjectStorage,
    now: datetime,
    *,
    limit: int,
    max_finalize_attempts: int = 5,
    after_promote: Callable[[], Awaitable[None]] | None = None,
) -> int:
    if max_finalize_attempts < 1:
        raise ValueError("max_finalize_attempts must be at least 1")
    finalized = 0
    for event in await metadata.list_waiting_outbox(limit):
        if event.staging_key is None:
            continue
        task = await metadata.get_task(event.task_id)
        if task is None:
            continue
        job = await metadata.get_job(task.job_id)
        if job is None or job.document_id is None:
            continue
        document = await metadata.get_document(job.document_id)
        if document is None:
            continue
        final_key = f"objects/{document.id}/source"
        try:
            await storage.promote(event.staging_key, final_key)
        except Exception:
            await metadata.record_finalization_failure(event.id, max_finalize_attempts, now)
            continue
        if after_promote is not None:
            await after_promote()
        if not await metadata.mark_object_ready(event.id, final_key, now):
            await storage.delete(final_key)
            continue
        finalized += 1
    return finalized


# 循环仅编排批量定稿；它不消费 NATS 消息，也不执行摄取 Task。
async def run_finalizer(
    metadata: MetadataRepository,
    storage: ObjectStorage,
    stop_event: asyncio.Event,
    *,
    interval_seconds: float,
    limit: int,
    max_finalize_attempts: int,
) -> None:
    """Finalize bounded batches and remain promptly interruptible while idle."""

    while not stop_event.is_set():
        await finalize_once(
            metadata,
            storage,
            datetime.now(UTC),
            limit=limit,
            max_finalize_attempts=max_finalize_attempts,
        )
        with suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
