"""Transactional outbox relay lifecycle skeleton."""

from __future__ import annotations

import asyncio


async def run_relay(stop_event: asyncio.Event) -> None:
    """Wait for shutdown without publishing tasks in Milestone A."""

    await stop_event.wait()
