"""Real MySQL migration verification."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import Connection, inspect, text

from rag_mvp.adapters.metadata.database import create_mysql_engine
from rag_mvp.adapters.metadata.migrate import run_migrations
from tests.unit.adapters.test_mysql_schema import EXPECTED_TABLES


@dataclass(frozen=True, slots=True)
class SchemaSnapshot:
    tables: set[str]
    unique_constraints: dict[str, set[tuple[str, ...]]]
    engines: dict[str, str]
    revision: str
    tenant_ids: tuple[str, ...]


def _inspect_constraints(
    connection: Connection,
) -> tuple[set[str], dict[str, set[tuple[str, ...]]]]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    unique_constraints = {
        table_name: {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(table_name)
        }
        for table_name in EXPECTED_TABLES
    }
    return tables, unique_constraints


async def _schema_snapshot(dsn: str) -> SchemaSnapshot:
    engine = create_mysql_engine(dsn)
    try:
        async with engine.connect() as connection:
            tables, unique_constraints = await connection.run_sync(_inspect_constraints)
            engine_rows = await connection.execute(
                text(
                    "SELECT table_name, engine FROM information_schema.tables "
                    "WHERE table_schema = DATABASE()"
                )
            )
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            tenant_rows = await connection.execute(text("SELECT id FROM tenants ORDER BY id"))
            return SchemaSnapshot(
                tables=tables,
                unique_constraints=unique_constraints,
                engines={str(row[0]): str(row[1]) for row in engine_rows},
                revision=str(revision),
                tenant_ids=tuple(str(row[0]) for row in tenant_rows),
            )
    finally:
        await engine.dispose()


@pytest.mark.integration
def test_upgrade_head_is_idempotent_and_creates_innodb_schema(
    mysql_dsn: str,
    migrations_root: Path,
) -> None:
    run_migrations(mysql_dsn, "upgrade", "head", migrations_root)
    run_migrations(mysql_dsn, "upgrade", "head", migrations_root)

    snapshot = asyncio.run(_schema_snapshot(mysql_dsn))

    assert snapshot.tables >= EXPECTED_TABLES
    assert snapshot.revision == "0001_core_schema"
    assert snapshot.tenant_ids == ("default_tenant",)
    assert all(snapshot.engines[table_name] == "InnoDB" for table_name in EXPECTED_TABLES)
    assert snapshot.unique_constraints["ingestion_fingerprints"] == {
        ("dataset_id", "file_sha256", "config_digest")
    }
    assert snapshot.unique_constraints["index_builds"] == {("document_id", "index_version")}
    assert snapshot.unique_constraints["idempotency_records"] == {
        ("operation_type", "idempotency_key")
    }
