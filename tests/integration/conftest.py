"""Shared configuration for real infrastructure integration tests."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from rag_mvp.adapters.metadata.database import create_mysql_engine, create_session_factory
from rag_mvp.adapters.metadata.migrate import run_migrations
from rag_mvp.adapters.metadata.mysql import MySQLMetadataRepository

MUTABLE_METADATA_TABLES = (
    "chunk_manifests",
    "index_builds",
    "outbox_events",
    "ingestion_fingerprints",
    "tasks",
    "jobs",
    "documents",
    "idempotency_records",
    "datasets",
)


@pytest.fixture
def mysql_dsn() -> str:
    """Use the host-published MySQL port unless Docker injects an override."""

    return os.getenv(
        "RAG_TEST_MYSQL_DSN",
        "mysql+asyncmy://rag:rag@127.0.0.1:3306/rag",
    )


@pytest.fixture
def migrations_root() -> Path:
    """Resolve migrations explicitly for host and non-editable test images."""

    configured = os.getenv("RAG_MIGRATIONS_ROOT", "").strip()
    return Path(configured) if configured else Path(__file__).resolve().parents[2]


async def reset_mysql_metadata(engine: AsyncEngine) -> None:
    """Clear only test-owned business rows while preserving migration and tenant state."""

    async with engine.connect() as connection:
        await connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        try:
            for table_name in MUTABLE_METADATA_TABLES:
                await connection.execute(text(f"TRUNCATE TABLE {table_name}"))
        finally:
            await connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))


@pytest_asyncio.fixture
async def mysql_repository(
    mysql_dsn: str,
    migrations_root: Path,
) -> AsyncIterator[tuple[MySQLMetadataRepository, AsyncEngine]]:
    """Provide one real repository with a clean schema for each integration test."""

    await asyncio.to_thread(run_migrations, mysql_dsn, "upgrade", "head", migrations_root)
    engine = create_mysql_engine(mysql_dsn)
    await reset_mysql_metadata(engine)
    repository = MySQLMetadataRepository(
        create_session_factory(engine),
        default_tenant_id="default_tenant",
    )
    try:
        yield repository, engine
    finally:
        await reset_mysql_metadata(engine)
        await engine.dispose()
