"""The sole future NATS consumer and ACK/NAK owner."""

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
from rag_mvp.ports.message_queue import TaskQueue
from rag_mvp.ports.metadata import MetadataRepository


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
) -> bool:
    """Consume one delivery and remain the sole owner of ACK/NAK decisions."""

    if max_deliveries < 1:
        raise ValueError("max_deliveries must be at least 1")
    delivery = await queue.consume(worker_id, timeout_seconds=0.0)
    if delivery is None:
        return False

    task = await metadata.get_task(delivery.task_id)
    if task is not None and task.type in {
        TaskType.CLEANUP_DOCUMENT,
        TaskType.CLEANUP_INDEX_VERSION,
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
            )
        else:
            result = await cleanup.execute(delivery.task_id, delivery.delivery_sequence, now)
    else:
        result = await ingestion.execute(delivery.task_id, delivery.delivery_sequence, now)
    if not result.claimed:
        await queue.ack(delivery)
        emit_event(
            "delivery_skipped",
            stage="worker_ack_terminal",
            duration_ms=0.0,
        )
        return True
    if result.completed:
        if after_complete is not None:
            await after_complete()
        await queue.ack(delivery)
        emit_event(
            "ingestion_completed",
            stage="worker_complete",
            duration_ms=0.0,
        )
        return True

    failure = result.failure or DomainFailure(
        code="INGESTION_RETRYABLE",
        message="ingestion did not complete",
        retryable=True,
    )
    delivery_number = delivery.redelivery_count + 1
    if failure.retryable and delivery_number < max_deliveries:
        await queue.nak(delivery, delay_seconds=0.0, error=failure)
        emit_event(
            "ingestion_retry_scheduled",
            stage="worker_nak",
            duration_ms=0.0,
            error_code=failure.code,
        )
        return True

    await metadata.fail_task(delivery.task_id, failure, now)
    await queue.ack(delivery)
    emit_event(
        "ingestion_failed",
        stage="worker_failed",
        duration_ms=0.0,
        error_code=failure.code,
    )
    return True


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
        )
        if not processed:
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=settings.worker_idle_interval_seconds,
                )


async def _run() -> None:
    settings = load_settings()
    container = await build_worker_container(settings)
    stop_event = asyncio.Event()
    install_shutdown_handlers(stop_event)
    try:
        await run_worker(settings, container, stop_event)
    finally:
        await container.close()


def main() -> None:
    """Run the Worker process."""

    asyncio.run(_run())


if __name__ == "__main__":
    main()
