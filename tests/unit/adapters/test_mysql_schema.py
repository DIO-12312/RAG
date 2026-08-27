"""Static contract for the MySQL metadata schema."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import JSON, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.mysql import DATETIME

from rag_mvp.adapters.metadata.tables import Base

EXPECTED_TABLES = {
    "tenants",
    "datasets",
    "documents",
    "ingestion_fingerprints",
    "jobs",
    "tasks",
    "outbox_events",
    "index_builds",
    "chunk_manifests",
    "idempotency_records",
}


def _column_names(constraint: UniqueConstraint | ForeignKeyConstraint) -> tuple[str, ...]:
    return tuple(column.name for column in constraint.columns)


def _unique_columns(table_name: str) -> set[tuple[str, ...]]:
    table = Base.metadata.tables[table_name]
    return {
        _column_names(constraint)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _foreign_key_targets(table_name: str) -> set[tuple[str, str]]:
    table = Base.metadata.tables[table_name]
    return {
        (foreign_key.parent.name, foreign_key.target_fullname) for foreign_key in table.foreign_keys
    }


def _all_columns() -> Iterable[tuple[str, object]]:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            yield column.name, column.type


def test_core_schema_declares_all_authoritative_tables_and_innodb() -> None:
    assert set(Base.metadata.tables) >= EXPECTED_TABLES
    assert all(
        Base.metadata.tables[name].dialect_options["mysql"]["engine"] == "InnoDB"
        for name in EXPECTED_TABLES
    )


def test_schema_declares_business_uniqueness_constraints() -> None:
    assert _unique_columns("ingestion_fingerprints") == {
        ("dataset_id", "file_sha256", "config_digest")
    }
    assert _unique_columns("index_builds") == {("document_id", "index_version")}
    assert _unique_columns("idempotency_records") == {("operation_type", "idempotency_key")}
    assert _unique_columns("chunk_manifests") == {("document_id", "index_version", "chunk_id")}
    assert _unique_columns("outbox_events") == {("task_id",)}


def test_schema_declares_aggregate_foreign_keys() -> None:
    assert ("tenant_id", "tenants.id") in _foreign_key_targets("datasets")
    assert ("dataset_id", "datasets.id") in _foreign_key_targets("documents")
    assert ("document_id", "documents.id") in _foreign_key_targets("jobs")
    assert ("dataset_id", "datasets.id") in _foreign_key_targets("jobs")
    assert ("job_id", "jobs.id") in _foreign_key_targets("tasks")
    assert ("task_id", "tasks.id") in _foreign_key_targets("outbox_events")
    assert ("document_id", "documents.id") in _foreign_key_targets("chunk_manifests")


def test_dataset_deletion_schema_tracks_lifecycle_and_dataset_ownership() -> None:
    datasets = Base.metadata.tables["datasets"].c
    jobs = Base.metadata.tables["jobs"].c
    idempotency = Base.metadata.tables["idempotency_records"].c

    assert {"status", "lifecycle_generation"} <= set(datasets.keys())
    assert jobs.document_id.nullable is True
    assert jobs.dataset_id.nullable is False
    assert idempotency.dataset_id.nullable is False


def test_schema_uses_precise_json_time_and_digest_columns_without_vectors() -> None:
    for table_name, column_name in (
        ("jobs", "error"),
        ("tasks", "error"),
        ("chunk_manifests", "locator"),
        ("chunk_manifests", "metadata_json"),
        ("idempotency_records", "result_json"),
    ):
        assert isinstance(Base.metadata.tables[table_name].c[column_name].type, JSON)

    datetime_columns = [
        column.type
        for table in Base.metadata.tables.values()
        for column in table.columns
        if column.name.endswith("_at")
    ]
    assert datetime_columns
    assert all(
        isinstance(column_type, DATETIME) and column_type.fsp == 6
        for column_type in datetime_columns
    )

    for table_name, column_name in (
        ("documents", "file_sha256"),
        ("ingestion_fingerprints", "file_sha256"),
        ("ingestion_fingerprints", "config_digest"),
        ("jobs", "config_digest"),
        ("chunk_manifests", "content_sha256"),
    ):
        digest_type = Base.metadata.tables[table_name].c[column_name].type
        assert digest_type.length == 64

    chunk_columns = set(Base.metadata.tables["chunk_manifests"].c.keys())
    assert "embedding" not in chunk_columns
    assert "vector" not in chunk_columns
