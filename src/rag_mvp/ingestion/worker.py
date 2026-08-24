"""The sole future NATS consumer and ACK/NAK owner."""

from __future__ import annotations

import asyncio

from rag_mvp.bootstrap.container import (
    Container,
    build_container,
    install_shutdown_handlers,
)
from rag_mvp.config import Settings, load_settings


async def run_worker(
    settings: Settings,
    container: Container,
    stop_event: asyncio.Event,
) -> None:
    """Wait for shutdown without consuming messages in Milestone A."""

    del settings, container
    await stop_event.wait()


async def _run() -> None:
    settings = load_settings()
    container = build_container(settings)
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
