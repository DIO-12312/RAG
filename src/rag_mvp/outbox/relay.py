"""以至少一次语义发布 READY 事务 Outbox 事件；消息内容仅为 task_id。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime

from rag_mvp.ports.message_queue import TaskQueue
from rag_mvp.ports.metadata import MetadataRepository


# 关键语义：发布成功、标记已发布前崩溃会再次发布同一 task_id；Worker 必须幂等处理。
async def relay_once(
    metadata: MetadataRepository,
    queue: TaskQueue,
    now: datetime,
    *,
    limit: int,
    after_publish: Callable[[], Awaitable[None]] | None = None,
) -> int:
    published = 0
    for event in await metadata.list_ready_outbox(limit):
        await queue.publish(event.task_id)
        if after_publish is not None:
            await after_publish()
        if await metadata.mark_outbox_published(event.id, now):
            published += 1
    return published


# Relay 只轮询 READY Outbox；不承担 ACK/NAK 或 Task 执行责任。
async def run_relay(
    metadata: MetadataRepository,
    queue: TaskQueue,
    stop_event: asyncio.Event,
    *,
    interval_seconds: float,
    limit: int,
    after_publish: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Relay bounded READY batches and remain promptly interruptible while idle."""

    while not stop_event.is_set():
        await relay_once(
            metadata,
            queue,
            datetime.now(UTC),
            limit=limit,
            after_publish=after_publish,
        )
        with suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
