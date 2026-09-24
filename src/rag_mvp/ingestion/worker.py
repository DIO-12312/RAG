"""唯一 NATS 消费者与 ACK/NAK 所有者，避免应用服务重复消费或确认消息。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from functools import partial

from rag_mvp.application.cleanup_service import CleanupService
from rag_mvp.application.ingestion_service import IngestionExecution, IngestionService
from rag_mvp.bootstrap.container import (
    Container,
    build_worker_container,
    install_shutdown_handlers,
)
from rag_mvp.config import Settings, load_settings
from rag_mvp.domain.enums import TaskType
from rag_mvp.domain.errors import DomainFailure
from rag_mvp.ingestion.checkpoints import Checkpoint
from rag_mvp.observability import emit_event
from rag_mvp.ports.message_queue import Delivery, TaskQueue
from rag_mvp.ports.metadata import MetadataRepository
from rag_mvp.telemetry import record_task, span


# 关键语义：无法条件认领的投递直接 ACK；已认领但可重试的失败才 NAK，
# 因而不会让取消、删除或旧 generation 的消息重新启动流水线。
async def worker_once(
    queue: TaskQueue,
    metadata: MetadataRepository,
    ingestion: IngestionService,
    worker_id: str,
    now: datetime,
    *,
    max_deliveries: int = 3,
    after_complete: Callable[[], Awaitable[None]] | None = None,
    cleanup: CleanupService | None = None,
    keepalive_interval_seconds: float = 20.0,
    retry_backoff_seconds: float = 5.0,
) -> bool:
    """Keep one independent trace open through execution, ACK/NAK, and logs."""

    if max_deliveries < 1:
        raise ValueError("max_deliveries must be at least 1")
    if keepalive_interval_seconds <= 0:
        raise ValueError("keepalive_interval_seconds must be positive")
    if retry_backoff_seconds < 0:
        raise ValueError("retry_backoff_seconds must not be negative")
    delivery = await queue.consume(worker_id, timeout_seconds=0.0)
    if delivery is None:
        return False

    with span("rag.worker.delivery"):
        return await _worker_once(
            queue,
            metadata,
            ingestion,
            worker_id,
            now,
            delivery,
            max_deliveries=max_deliveries,
            after_complete=after_complete,
            cleanup=cleanup,
            keepalive_interval_seconds=keepalive_interval_seconds,
            retry_backoff_seconds=retry_backoff_seconds,
        )


async def _worker_once(
    queue: TaskQueue,
    metadata: MetadataRepository,
    ingestion: IngestionService,
    worker_id: str,
    now: datetime,
    delivery: Delivery,
    *,
    max_deliveries: int = 3,
    after_complete: Callable[[], Awaitable[None]] | None = None,
    cleanup: CleanupService | None = None,
    keepalive_interval_seconds: float = 20.0,
    retry_backoff_seconds: float = 5.0,
) -> bool:
    """Consume one delivery and remain the sole owner of ACK/NAK decisions."""

    task = await metadata.get_task(delivery.task_id)
    # 长文档摄取会跑满数分钟；不续约时 JetStream 会在 ack_wait 后重投同一 Task，
    # 让同一份文档被反复解析与向量化，限流窗口里越重试越失败。
    keepalive = asyncio.create_task(
        _keep_delivery_alive(queue, delivery, keepalive_interval_seconds)
    )
    try:
        if task is not None and task.type in {
            TaskType.CLEANUP_DOCUMENT,
            TaskType.CLEANUP_INDEX_VERSION,
            TaskType.CLEANUP_DATASET,
        }:
            if cleanup is None:
                result = IngestionExecution(
                    claimed=True,
                    completed=False,
                    failure=DomainFailure(
                        "CLEANUP_SERVICE_UNAVAILABLE",
                        "cleanup service is not configured",
                        retryable=True,
                    ),
                    job_id=task.job_id,
                )
            else:
                with span(
                    "rag.worker.cleanup",
                    **{"task.id": delivery.task_id, "delivery.count": delivery.redelivery_count},
                ):
                    result = await cleanup.execute(
                        delivery.task_id, delivery.delivery_sequence, now
                    )
        else:
            with span(
                "rag.worker.ingestion",
                **{"task.id": delivery.task_id, "delivery.count": delivery.redelivery_count},
            ):
                result = await ingestion.execute(delivery.task_id, delivery.delivery_sequence, now)
    finally:
        keepalive.cancel()
        with suppress(asyncio.CancelledError):
            await keepalive

    job_id = result.job_id or (task.job_id if task is not None else None)
    if not result.claimed:
        await queue.ack(delivery)
        record_task("complete", "skipped")
        emit_event(
            "delivery_skipped",
            stage="worker_ack_terminal",
            duration_ms=0.0,
            job_id=job_id,
            document_id=result.document_id,
            dataset_id=result.dataset_id,
            index_version=result.index_version,
        )
        return True
    if result.completed:
        if after_complete is not None:
            await after_complete()
        await queue.ack(delivery)
        record_task("complete", "succeeded")
        emit_event(
            "ingestion_completed",
            stage="worker_complete",
            duration_ms=0.0,
            job_id=job_id,
            document_id=result.document_id,
            dataset_id=result.dataset_id,
            index_version=result.index_version,
        )
        return True

    failure = result.failure or DomainFailure(
        code="INGESTION_RETRYABLE",
        message="ingestion did not complete",
        retryable=True,
    )
    delivery_number = delivery.redelivery_count + 1
    delay = retry_backoff_seconds * (2 ** (delivery_number - 1))
    if task is not None and task.type is TaskType.CLEANUP_DATASET and failure.retryable:
        await queue.nak(delivery, delay_seconds=delay, error=failure)
        record_task("complete", "retry")
        emit_event(
            "dataset_cleanup_retry_scheduled",
            stage="worker_nak",
            duration_ms=0.0,
            error_code=failure.code,
            job_id=job_id,
            document_id=result.document_id,
            dataset_id=result.dataset_id,
            index_version=result.index_version,
        )
        return True
    if failure.retryable and delivery_number < max_deliveries:
        await queue.nak(delivery, delay_seconds=delay, error=failure)
        record_task("complete", "retry")
        emit_event(
            "ingestion_retry_scheduled",
            stage="worker_nak",
            duration_ms=0.0,
            error_code=failure.code,
            failure_message=failure.message[:200],
            retry_in_seconds=delay,
            job_id=job_id,
            document_id=result.document_id,
            dataset_id=result.dataset_id,
            index_version=result.index_version,
        )
        return True

    await metadata.fail_task(delivery.task_id, failure, now)
    await queue.ack(delivery)
    record_task("complete", "failed")
    emit_event(
        "ingestion_failed",
        stage="worker_failed",
        duration_ms=0.0,
        error_code=failure.code,
        failure_message=failure.message[:200],
        job_id=job_id,
        document_id=result.document_id,
        dataset_id=result.dataset_id,
        index_version=result.index_version,
    )
    return True


# 在长耗时执行期间周期性续约投递，避免 JetStream 按 ack_wait 判定超时并重投。
async def _keep_delivery_alive(
    queue: TaskQueue,
    delivery: Delivery,
    interval_seconds: float,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        await queue.in_progress(delivery)


# Worker 是唯一 NATS 消费与确认边界，应用服务不得绕开此循环直接 ACK/NAK。
async def run_worker(
    settings: Settings,
    container: Container,
    stop_event: asyncio.Event,
) -> None:
    """Consume one delivery at a time until graceful shutdown is requested."""

    queue = container.queue
    metadata = container.metadata
    ingestion = container.ingestion
    if queue is None or metadata is None or ingestion is None:
        raise RuntimeError("worker container is missing required services")
    after_complete = (
        partial(container.failpoint, Checkpoint.AFTER_COMPLETE_BEFORE_ACK)
        if container.failpoint is not None
        else None
    )
    while not stop_event.is_set():
        processed = await worker_once(
            queue,
            metadata,
            ingestion,
            settings.nats_consumer,
            datetime.now(UTC),
            max_deliveries=settings.nats_max_deliver,
            after_complete=after_complete,
            cleanup=container.cleanup,
            keepalive_interval_seconds=settings.worker_keepalive_seconds,
            retry_backoff_seconds=settings.nats_retry_backoff_seconds,
        )
        if not processed:
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=settings.worker_idle_interval_seconds,
                )


# 装配 Worker 依赖、注册退出信号，并在退出时关闭容器。
async def _run() -> None:
    settings = load_settings()
    container = await build_worker_container(settings)
    stop_event = asyncio.Event()
    install_shutdown_handlers(stop_event)
    try:
        await run_worker(settings, container, stop_event)
    finally:
        await container.close()


# 控制台入口：解析运行环境后启动对应进程。
def main() -> None:
    """Run the Worker process."""

    asyncio.run(_run())


if __name__ == "__main__":
    main()
