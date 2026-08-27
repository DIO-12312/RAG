from __future__ import annotations

# 验证 staging object 提升、Outbox 就绪和失败补偿逻辑。
from datetime import UTC, datetime

import pytest

from rag_mvp.application.document_service import DocumentService
from rag_mvp.application.dto import CreateDatasetCommand, SubmitDocumentCommand
from rag_mvp.outbox.finalizer import finalize_once
from tests.fakes.metadata import FakeMetadataRepository
from tests.fakes.storage import FakeObjectStorage


@pytest.mark.asyncio
async def test_finalizer_promotes_object_before_outbox_becomes_ready() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    storage = FakeObjectStorage()
    service = DocumentService(repository, storage, max_upload_bytes=1024)
    await service.create_dataset(
        CreateDatasetCommand("trace", "create", "Docs", "fake", 8, now, "dataset-1")
    )
    result = await service.submit_document(
        SubmitDocumentCommand(
            "trace",
            "submit",
            "dataset-1",
            "guide.txt",
            b"hello",
            None,
            None,
            "text-v1",
            800,
            120,
            "fake",
            now,
        )
    )

    assert await repository.list_ready_outbox(limit=10) == ()
    assert await finalize_once(repository, storage, now, limit=10) == 1

    ready = await repository.list_ready_outbox(limit=10)
    document = await repository.get_document(result.document_id)
    assert len(ready) == 1
    assert document is not None
    assert document.object_key is not None
    assert await storage.exists(document.object_key) is True
    assert await storage.exists(service.staging_key("submit")) is False
