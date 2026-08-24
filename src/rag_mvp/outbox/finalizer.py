"""Object finalizer lifecycle skeleton."""

from __future__ import annotations

import asyncio


async def run_finalizer(stop_event: asyncio.Event) -> None:
    """Wait for shutdown without touching staging objects in Milestone A."""

    await stop_event.wait()
