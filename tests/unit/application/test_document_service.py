from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rag_mvp.application.document_service import DocumentService
from rag_mvp.application.dto import (
    CreateDatasetCommand,
    DeleteDatasetCommand,
    SubmitDocumentCommand,
)
from rag_mvp.domain.errors import DomainError
from rag_mvp.domain.ids import file_sha256
from tests.fakes.metadata import FakeMetadataRepository, InjectedRepositoryFailure
from tests.fakes.storage import FakeObjectStorage


def test_delete_dataset_command_carries_idempotency_and_dataset_scope() -> None:
    now = datetime.now(UTC)
    command = DeleteDatasetCommand("request", "delete-key", "dataset-1", now)

    assert command.idempotency_key == "delete-key"
    assert command.dataset_id == "dataset-1"
    assert command.now is now


def _submit(
    *,
    idempotency_key: str = "request-1",
    content: bytes = b"hello",
    expected_sha256: str | None = None,
) -> SubmitDocumentCommand:
    return SubmitDocumentCommand(
        request_id="trace-1",
        idempotency_key=idempotency_key,
        dataset_id="dataset-1",
        source_name="guide.txt",
        content=content,
        expected_sha256=expected_sha256,
        target_document_id=None,
        parser_version="text-v1",
        chunk_size=800,
        chunk_overlap=120,
        embedding_model="fake-embedding",
        now=datetime.now(UTC),
    )


async def _service(
    max_upload_bytes: int = 1024,
) -> tuple[DocumentService, FakeMetadataRepository, FakeObjectStorage]:
    repository = FakeMetadataRepository()
    storage = FakeObjectStorage()
    service = DocumentService(
        repository,
        storage,
        max_upload_bytes=max_upload_bytes,
        default_tenant_id="default_tenant",
        embedding_model="fake-embedding",
        embedding_dimension=8,
    )
    await service.create_dataset(
        CreateDatasetCommand(
            request_id="trace-create",
            idempotency_key="create-1",
            name="Docs",
            embedding_model="fake-embedding",
            embedding_dimension=8,
            now=datetime.now(UTC),
            dataset_id="dataset-1",
        )
    )
    return service, repository, storage


@pytest.mark.asyncio
async def test_create_dataset_rejects_runtime_embedding_mismatch() -> None:
    service = DocumentService(
        FakeMetadataRepository(),
        FakeObjectStorage(),
        max_upload_bytes=1024,
        default_tenant_id="default_tenant",
        embedding_model="production-embedding",
        embedding_dimension=1024,
    )

    with pytest.raises(DomainError) as error:
        await service.create_dataset(
            CreateDatasetCommand(
                request_id="trace-create",
                idempotency_key="create-mismatch",
                name="Docs",
                embedding_model="other-embedding",
                embedding_dimension=768,
                now=datetime.now(UTC),
            )
        )

    assert error.value.failure.code == "EMBEDDING_CONFIG_MISMATCH"


@pytest.mark.asyncio
async def test_submit_writes_staging_and_atomically_creates_waiting_work() -> None:
    service, repository, storage = await _service()

    result = await service.submit_document(_submit())

    assert result.reused is False
    assert await storage.exists(service.staging_key("request-1")) is True
    assert repository.counts()["tasks"] == 1
    assert len(await repository.list_waiting_outbox(limit=10)) == 1


@pytest.mark.asyncio
async def test_same_file_different_key_reuses_canonical_job_and_cleans_loser_staging() -> None:
    service, repository, storage = await _service()

    first = await service.submit_document(_submit(idempotency_key="request-1"))
    duplicate = await service.submit_document(_submit(idempotency_key="request-2"))

    assert duplicate.reused is True
    assert duplicate.document_id == first.document_id
    assert duplicate.job_id == first.job_id
    assert await storage.exists(service.staging_key("request-2")) is False
    assert repository.counts()["documents"] == 1


@pytest.mark.asyncio
async def test_same_idempotency_key_with_different_bytes_is_rejected_without_overwrite() -> None:
    service, _, storage = await _service()
    await service.submit_document(_submit(content=b"first"))

    with pytest.raises(DomainError) as error:
        await service.submit_document(_submit(content=b"second"))

    assert error.value.failure.code == "IDEMPOTENCY_CONFLICT"
    assert await storage.read(service.staging_key("request-1")) == b"first"


@pytest.mark.asyncio
async def test_sha_and_size_validation_happen_before_metadata_creation() -> None:
    service, repository, _ = await _service(max_upload_bytes=4)

    with pytest.raises(DomainError) as oversized:
        await service.submit_document(_submit(content=b"12345"))
    assert oversized.value.failure.code == "UPLOAD_TOO_LARGE"

    with pytest.raises(DomainError) as mismatch:
        await service.submit_document(_submit(content=b"1234", expected_sha256="0" * 64))
    assert mismatch.value.failure.code == "SHA256_MISMATCH"
    assert repository.counts()["tasks"] == 0


@pytest.mark.asyncio
async def test_repository_failure_cleans_staging_object() -> None:
    service, repository, storage = await _service()
    repository.fail_next_submit = True

    with pytest.raises(InjectedRepositoryFailure):
        await service.submit_document(_submit(expected_sha256=file_sha256(b"hello")))

    assert await storage.exists(service.staging_key("request-1")) is False
    assert repository.counts()["tasks"] == 0
