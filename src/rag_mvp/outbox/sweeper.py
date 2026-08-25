"""TTL cleanup for unreferenced staging objects."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from rag_mvp.ports.metadata import MetadataRepository
from rag_mvp.ports.storage import ObjectStorage


async def sweep_staging_once(
    metadata: MetadataRepository,
    storage: ObjectStorage,
    *,
    older_than: datetime,
) -> int:
    referenced = set(await metadata.waiting_staging_keys())
    deleted = 0
    for stored in await storage.list_objects("staging"):
        if stored.modified_at > older_than or stored.key in referenced:
            continue
        await storage.delete(stored.key)
        deleted += 1
    return deleted


async def run_staging_sweeper(
    metadata: MetadataRepository,
    storage: ObjectStorage,
    stop_event: asyncio.Event,
    *,
    interval_seconds: float,
    ttl_seconds: float,
) -> None:
    """Periodically remove only expired staging objects not referenced by WAITING Outbox."""

    while not stop_event.is_set():
        older_than = datetime.now(UTC) - timedelta(seconds=ttl_seconds)
        await sweep_staging_once(metadata, storage, older_than=older_than)
        with suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
