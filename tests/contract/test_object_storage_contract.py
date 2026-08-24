from __future__ import annotations

import pytest

from tests.fakes.storage import FakeObjectStorage


@pytest.mark.asyncio
async def test_object_storage_write_promote_read_and_delete_are_idempotent() -> None:
    storage = FakeObjectStorage()

    await storage.write("staging/request-1", b"hello")
    promoted = await storage.promote("staging/request-1", "objects/document-1")
    repeated = await storage.promote("staging/request-1", "objects/document-1")

    assert promoted == "objects/document-1"
    assert repeated == promoted
    assert await storage.read(promoted) == b"hello"
    assert await storage.exists("staging/request-1") is False
    assert await storage.exists(promoted) is True
    await storage.delete(promoted)
    await storage.delete(promoted)
    assert await storage.exists(promoted) is False
