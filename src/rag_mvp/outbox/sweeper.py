"""TTL cleanup for unreferenced staging objects."""

from __future__ import annotations

from datetime import datetime

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
