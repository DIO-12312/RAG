"""Promote staging objects before making their Outbox events publishable."""

from __future__ import annotations

import asyncio
from datetime import datetime

from rag_mvp.ports.metadata import MetadataRepository
from rag_mvp.ports.storage import ObjectStorage


async def finalize_once(
    metadata: MetadataRepository,
    storage: ObjectStorage,
    now: datetime,
    *,
    limit: int,
) -> int:
    finalized = 0
    for event in await metadata.list_waiting_outbox(limit):
        if event.staging_key is None:
            continue
        task = await metadata.get_task(event.task_id)
        if task is None:
            continue
        job = await metadata.get_job(task.job_id)
        if job is None:
            continue
        document = await metadata.get_document(job.document_id)
        if document is None:
            continue
        final_key = f"objects/{document.id}/source"
        await storage.promote(event.staging_key, final_key)
        if not await metadata.mark_object_ready(event.id, final_key, now):
            await storage.delete(final_key)
            continue
        finalized += 1
    return finalized


async def run_finalizer(stop_event: asyncio.Event) -> None:
    """Wait for shutdown; process wiring is introduced by the functional container."""

    await stop_event.wait()
