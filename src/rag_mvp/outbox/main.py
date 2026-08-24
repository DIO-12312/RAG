"""Independent Object Finalizer and Outbox Relay process."""

from __future__ import annotations

import asyncio
from functools import partial

from rag_mvp.bootstrap.container import (
    Container,
    build_outbox_container,
    install_shutdown_handlers,
)
from rag_mvp.config import Settings, load_settings
from rag_mvp.ingestion.checkpoints import Checkpoint
from rag_mvp.outbox.finalizer import run_finalizer
from rag_mvp.outbox.relay import run_relay
from rag_mvp.outbox.sweeper import run_staging_sweeper


async def run_outbox(
    settings: Settings,
    container: Container,
    stop_event: asyncio.Event,
) -> None:
    """Run Finalizer, Relay, and staging Sweeper without consuming tasks."""

    metadata = container.metadata
    storage = container.storage
    queue = container.queue
    if metadata is None or storage is None or queue is None:
        raise RuntimeError("outbox container is missing required ports")
    async with asyncio.TaskGroup() as task_group:
        task_group.create_task(
            run_finalizer(
                metadata,
                storage,
                stop_event,
                interval_seconds=settings.outbox_poll_interval_seconds,
                limit=settings.outbox_batch_size,
                max_finalize_attempts=settings.max_finalize_attempts,
            )
        )
        task_group.create_task(
            run_relay(
                metadata,
                queue,
                stop_event,
                interval_seconds=settings.outbox_poll_interval_seconds,
                limit=settings.outbox_batch_size,
                after_publish=(
                    partial(
                        container.failpoint,
                        Checkpoint.AFTER_RELAY_PUBLISH_BEFORE_MARK,
                    )
                    if container.failpoint is not None
                    else None
                ),
            )
        )
        task_group.create_task(
            run_staging_sweeper(
                metadata,
                storage,
                stop_event,
                interval_seconds=settings.staging_sweep_interval_seconds,
                ttl_seconds=settings.staging_ttl_seconds,
            )
        )


async def _run() -> None:
    settings = load_settings()
    container = await build_outbox_container(settings)
    stop_event = asyncio.Event()
    install_shutdown_handlers(stop_event)
    try:
        await run_outbox(settings, container, stop_event)
    finally:
        await container.close()


def main() -> None:
    """Run the independent Outbox process."""

    asyncio.run(_run())


if __name__ == "__main__":
    main()
