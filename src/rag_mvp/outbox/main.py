"""Independent Object Finalizer and Outbox Relay process."""

from __future__ import annotations

import asyncio

from rag_mvp.bootstrap.container import (
    Container,
    build_container,
    install_shutdown_handlers,
)
from rag_mvp.config import Settings, load_settings
from rag_mvp.outbox.finalizer import run_finalizer
from rag_mvp.outbox.relay import run_relay


async def run_outbox(
    settings: Settings,
    container: Container,
    stop_event: asyncio.Event,
) -> None:
    """Run empty finalizer and relay lifecycles without consuming tasks."""

    del settings, container
    async with asyncio.TaskGroup() as task_group:
        task_group.create_task(run_finalizer(stop_event))
        task_group.create_task(run_relay(stop_event))


async def _run() -> None:
    settings = load_settings()
    container = build_container(settings)
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
