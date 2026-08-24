"""Create the authoritative RAG metadata schema.

Revision ID: 0001_core_schema
Revises: None
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0001_core_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id() -> mysql.VARCHAR:
    return mysql.VARCHAR(36, charset="ascii", collation="ascii_bin")


def _digest() -> mysql.VARCHAR:
    return mysql.VARCHAR(64, charset="ascii", collation="ascii_bin")


def _timestamp() -> mysql.DATETIME:
    return mysql.DATETIME(fsp=6)


def _created_at() -> sa.Column[datetime]:
    return sa.Column(
        "created_at",
        _timestamp(),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP(6)"),
    )


def _updated_at() -> sa.Column[datetime]:
    return sa.Column(
        "updated_at",
        _timestamp(),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP(6)"),
    )


def _create_table(name: str, *elements: Any) -> None:
    op.create_table(
        name,
        *elements,
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )


def upgrade() -> None:
    """Create all tables, constraints, indexes, and the MVP tenant."""

    _create_table(
        "tenants",
        sa.Column("id", sa.String(length=128), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
    )
    op.bulk_insert(
        sa.table("tenants", sa.column("id", sa.String(length=128))),
        [{"id": "default_tenant"}],
    )

    _create_table(
        "datasets",
        sa.Column("id", _id(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("embedding_model", sa.String(length=255), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("search_schema_version", sa.Integer(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "embedding_dimension > 0",
            name="ck_datasets_embedding_dimension_positive",
        ),
        sa.CheckConstraint(
            "search_schema_version > 0",
            name="ck_datasets_search_schema_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_datasets_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_datasets"),
    )
    op.create_index("ix_datasets_tenant_id", "datasets", ["tenant_id"], unique=False)

    _create_table(
        "documents",
        sa.Column("id", _id(), nullable=False),
        sa.Column("dataset_id", _id(), nullable=False),
        sa.Column("source_name", sa.String(length=1024), nullable=False),
        sa.Column("file_sha256", _digest(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("active_version", sa.Integer(), nullable=True),
        sa.Column("next_index_version", sa.Integer(), nullable=False),
        sa.Column("lifecycle_generation", sa.BigInteger(), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "active_version IS NULL OR active_version > 0",
            name="ck_documents_active_version_positive",
        ),
        sa.CheckConstraint(
            "next_index_version > 0",
            name="ck_documents_next_index_version_positive",
        ),
        sa.CheckConstraint(
            "lifecycle_generation >= 0",
            name="ck_documents_lifecycle_generation_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name="fk_documents_dataset_id_datasets",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
    )
    op.create_index(
        "ix_documents_dataset_status",
        "documents",
        ["dataset_id", "status"],
        unique=False,
    )

    _create_table(
        "jobs",
        sa.Column("id", _id(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("document_id", _id(), nullable=False),
        sa.Column("config_digest", _digest(), nullable=False),
        sa.Column("index_version", sa.Integer(), nullable=False),
        sa.Column("document_generation", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("progress", sa.Numeric(precision=7, scale=6), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("cancel_requested_at", _timestamp(), nullable=True),
        sa.Column("retry_of_job_id", _id(), nullable=True),
        sa.Column("active_retry_parent_id", _id(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("index_version > 0", name="ck_jobs_index_version_positive"),
        sa.CheckConstraint(
            "document_generation >= 0",
            name="ck_jobs_document_generation_non_negative",
        ),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 1",
            name="ck_jobs_progress_range",
        ),
        sa.CheckConstraint("retry_count >= 0", name="ck_jobs_retry_count_non_negative"),
        sa.ForeignKeyConstraint(
            ["active_retry_parent_id"],
            ["jobs.id"],
            name="fk_jobs_active_retry_parent_id_jobs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_jobs_document_id_documents",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["retry_of_job_id"],
            ["jobs.id"],
            name="fk_jobs_retry_of_job_id_jobs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
        sa.UniqueConstraint(
            "active_retry_parent_id",
            name="uq_jobs_active_retry_parent_id",
        ),
    )
    op.create_index(
        "ix_jobs_document_status",
        "jobs",
        ["document_id", "status"],
        unique=False,
    )

    _create_table(
        "tasks",
        sa.Column("id", _id(), nullable=False),
        sa.Column("job_id", _id(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("last_delivery_sequence", sa.BigInteger(), nullable=True),
        sa.Column("checkpoint", sa.String(length=64), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("attempt >= 0", name="ck_tasks_attempt_non_negative"),
        sa.CheckConstraint(
            "last_delivery_sequence IS NULL OR last_delivery_sequence > 0",
            name="ck_tasks_last_delivery_sequence_positive",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_tasks_job_id_jobs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tasks"),
    )
    op.create_index("ix_tasks_job_status", "tasks", ["job_id", "status"], unique=False)

    _create_table(
        "ingestion_fingerprints",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("dataset_id", _id(), nullable=False),
        sa.Column("file_sha256", _digest(), nullable=False),
        sa.Column("config_digest", _digest(), nullable=False),
        sa.Column("document_id", _id(), nullable=False),
        sa.Column("job_id", _id(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        _created_at(),
        _updated_at(),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name="fk_ingestion_fingerprints_dataset_id_datasets",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_ingestion_fingerprints_document_id_documents",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_ingestion_fingerprints_job_id_jobs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_fingerprints"),
        sa.UniqueConstraint(
            "dataset_id",
            "file_sha256",
            "config_digest",
            name="uq_ingestion_fingerprints_dataset_file_config",
        ),
    )
    op.create_index(
        "ix_ingestion_fingerprints_document",
        "ingestion_fingerprints",
        ["document_id"],
        unique=False,
    )

    _create_table(
        "outbox_events",
        sa.Column("id", _id(), nullable=False),
        sa.Column("task_id", _id(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("staging_key", sa.String(length=1024), nullable=True),
        sa.Column("published_at", _timestamp(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("attempt >= 0", name="ck_outbox_events_attempt_non_negative"),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name="fk_outbox_events_task_id_tasks",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_events"),
        sa.UniqueConstraint("task_id", name="uq_outbox_events_task_id"),
    )
    op.create_index(
        "ix_outbox_events_status_created",
        "outbox_events",
        ["status", "created_at"],
        unique=False,
    )

    _create_table(
        "index_builds",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("document_id", _id(), nullable=False),
        sa.Column("index_version", sa.Integer(), nullable=False),
        sa.Column("job_id", _id(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "index_version > 0",
            name="ck_index_builds_index_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_index_builds_document_id_documents",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_index_builds_job_id_jobs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_index_builds"),
        sa.UniqueConstraint(
            "document_id",
            "index_version",
            name="uq_index_builds_document_version",
        ),
    )

    _create_table(
        "chunk_manifests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("document_id", _id(), nullable=False),
        sa.Column("index_version", sa.Integer(), nullable=False),
        sa.Column(
            "chunk_id",
            mysql.VARCHAR(16, charset="ascii", collation="ascii_bin"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content_sha256", _digest(), nullable=False),
        sa.Column("source_name", sa.String(length=1024), nullable=False),
        sa.Column("locator", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "index_version > 0",
            name="ck_chunk_manifests_index_version_positive",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_chunk_manifests_ordinal_non_negative"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_chunk_manifests_document_id_documents",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chunk_manifests"),
        sa.UniqueConstraint(
            "document_id",
            "index_version",
            "chunk_id",
            name="uq_chunk_manifests_document_version_chunk",
        ),
    )

    _create_table(
        "idempotency_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_digest", _digest(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_idempotency_records"),
        sa.UniqueConstraint(
            "operation_type",
            "idempotency_key",
            name="uq_idempotency_records_operation_key",
        ),
    )


def downgrade() -> None:
    """Drop the schema in reverse dependency order."""

    op.drop_table("idempotency_records")
    op.drop_table("chunk_manifests")
    op.drop_table("index_builds")
    op.drop_table("outbox_events")
    op.drop_table("ingestion_fingerprints")
    op.drop_table("tasks")
    op.drop_table("jobs")
    op.drop_table("documents")
    op.drop_table("datasets")
    op.drop_table("tenants")
