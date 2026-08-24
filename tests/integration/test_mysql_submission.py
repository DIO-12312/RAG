"""Real MySQL tests for upload idempotency and transaction atomicity."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine

from rag_mvp.adapters.metadata.mysql import MySQLMetadataRepository
from rag_mvp.domain.errors import DomainError
from rag_mvp.domain.models import Dataset
from rag_mvp.ports.metadata import SubmitIngestion


def _dataset(now: datetime) -> Dataset:
    return Dataset(
        id="dataset-1",
        tenant_id="default_tenant",
        name="Docs",
        embedding_model="fake-embedding",
        embedding_dimension=8,
        created_at=now,
    )


def _submission(*, idempotency_key: str, staging_key: str, now: datetime) -> SubmitIngestion:
    return SubmitIngestion(
        idempotency_key=idempotency_key,
        dataset_id="dataset-1",
        source_name="guide.txt",
        staging_key=staging_key,
        file_sha256="a" * 64,
        config_digest="b" * 64,
        now=now,
    )


async def _counts(engine: AsyncEngine) -> dict[str, int]:
    async with engine.connect() as connection:
        return {
            table_name: int(
                await connection.scalar(text(f"SELECT COUNT(*) FROM {table_name}")) or 0
            )
            for table_name in (
                "documents",
                "ingestion_fingerprints",
                "jobs",
                "tasks",
                "outbox_events",
                "index_builds",
                "idempotency_records",
            )
        }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_same_fingerprint_creates_one_canonical_task_and_outbox(
    mysql_repository: tuple[MySQLMetadataRepository, AsyncEngine],
) -> None:
    repository, engine = mysql_repository
    now = datetime.now(UTC)
    await repository.create_dataset(_dataset(now))

    first, second = await asyncio.gather(
        repository.submit_ingestion(
            _submission(idempotency_key="request-a", staging_key="staging/a", now=now)
        ),
        repository.submit_ingestion(
            _submission(idempotency_key="request-b", staging_key="staging/b", now=now)
        ),
    )

    assert first.document_id == second.document_id
    assert first.job_id == second.job_id
    assert first.task_id == second.task_id
    assert {first.reused, second.reused} == {False, True}
    assert {first.staging_referenced, second.staging_referenced} == {False, True}
    assert await _counts(engine) == {
        "documents": 1,
        "ingestion_fingerprints": 1,
        "jobs": 1,
        "tasks": 1,
        "outbox_events": 1,
        "index_builds": 1,
        "idempotency_records": 2,
    }
    assert await repository.get_dataset("dataset-1") is not None
    assert await repository.get_document(first.document_id) is not None
    assert await repository.get_job(first.job_id) is not None
    assert await repository.get_task(first.task_id) is not None
    task_for_job = await repository.get_task_for_job(first.job_id)
    assert task_for_job is not None and task_for_job.id == first.task_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_exception_before_commit_rolls_back_all_submission_rows(
    mysql_repository: tuple[MySQLMetadataRepository, AsyncEngine],
) -> None:
    repository, engine = mysql_repository
    now = datetime.now(UTC)
    await repository.create_dataset(_dataset(now))

    def fail_before_outbox_insert(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if "insert into outbox_events" in statement.lower():
            raise RuntimeError("injected failure before transaction commit")

    event.listen(engine.sync_engine, "before_cursor_execute", fail_before_outbox_insert)
    try:
        with pytest.raises(RuntimeError, match="injected failure"):
            await repository.submit_ingestion(
                _submission(idempotency_key="request-a", staging_key="staging/a", now=now)
            )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", fail_before_outbox_insert)

    assert await _counts(engine) == {
        "documents": 0,
        "ingestion_fingerprints": 0,
        "jobs": 0,
        "tasks": 0,
        "outbox_events": 0,
        "index_builds": 0,
        "idempotency_records": 0,
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_same_idempotency_key_replays_result_and_rejects_changed_command(
    mysql_repository: tuple[MySQLMetadataRepository, AsyncEngine],
) -> None:
    repository, engine = mysql_repository
    now = datetime.now(UTC)
    await repository.create_dataset(_dataset(now))
    command = _submission(idempotency_key="request-a", staging_key="staging/a", now=now)

    first = await repository.submit_ingestion(command)
    repeated = await repository.submit_ingestion(command)

    assert repeated.document_id == first.document_id
    assert repeated.job_id == first.job_id
    assert repeated.task_id == first.task_id
    assert repeated.reused is True
    assert repeated.staging_referenced is False

    with pytest.raises(DomainError) as error:
        await repository.submit_ingestion(replace(command, file_sha256="c" * 64))

    assert error.value.failure.code == "IDEMPOTENCY_KEY_REUSED"
    counts = await _counts(engine)
    assert counts["documents"] == 1
    assert counts["tasks"] == 1
    assert counts["outbox_events"] == 1
    assert counts["idempotency_records"] == 1
