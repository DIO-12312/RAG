"""SQLAlchemy mappings for authoritative MySQL metadata."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import DATETIME, VARCHAR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

ID = VARCHAR(36, charset="ascii", collation="ascii_bin")
DIGEST = VARCHAR(64, charset="ascii", collation="ascii_bin")
TIMESTAMP = DATETIME(fsp=6)
MYSQL_TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


class Base(DeclarativeBase):
    """Base mapping shared by migrations and the repository adapter."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """Database timestamps stored as UTC-naive values with microsecond precision."""

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"),
        server_onupdate=text("CURRENT_TIMESTAMP(6)"),
    )


class TenantTable(Base):
    __tablename__ = "tenants"
    __table_args__ = MYSQL_TABLE_OPTIONS

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )


class DatasetTable(TimestampMixin, Base):
    __tablename__ = "datasets"
    __table_args__ = (
        CheckConstraint("embedding_dimension > 0", name="embedding_dimension_positive"),
        CheckConstraint("search_schema_version > 0", name="search_schema_version_positive"),
        CheckConstraint("lifecycle_generation >= 0", name="lifecycle_generation_non_negative"),
        Index("ix_datasets_tenant_status", "tenant_id", "status"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    search_schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    lifecycle_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class DocumentTable(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "active_version IS NULL OR active_version > 0",
            name="active_version_positive",
        ),
        CheckConstraint("next_index_version > 0", name="next_index_version_positive"),
        CheckConstraint("lifecycle_generation >= 0", name="lifecycle_generation_non_negative"),
        Index("ix_documents_dataset_status", "dataset_id", "status"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(
        ID,
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_name: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_sha256: Mapped[str] = mapped_column(DIGEST, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    active_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_index_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lifecycle_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)


class JobTable(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("active_retry_parent_id", name="uq_jobs_active_retry_parent_id"),
        CheckConstraint("index_version > 0", name="index_version_positive"),
        CheckConstraint("document_generation >= 0", name="document_generation_non_negative"),
        CheckConstraint("progress >= 0 AND progress <= 1", name="progress_range"),
        CheckConstraint("retry_count >= 0", name="retry_count_non_negative"),
        Index("ix_jobs_document_status", "document_id", "status"),
        Index("ix_jobs_dataset_status", "dataset_id", "status"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_id: Mapped[str] = mapped_column(
        ID,
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    document_id: Mapped[str | None] = mapped_column(
        ID,
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    config_digest: Mapped[str] = mapped_column(DIGEST, nullable=False)
    index_version: Mapped[int] = mapped_column(Integer, nullable=False)
    document_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    progress: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False, default=0)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    retry_of_job_id: Mapped[str | None] = mapped_column(
        ID,
        ForeignKey("jobs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    active_retry_parent_id: Mapped[str | None] = mapped_column(
        ID,
        ForeignKey("jobs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class TaskTable(TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("attempt >= 0", name="attempt_non_negative"),
        CheckConstraint(
            "last_delivery_sequence IS NULL OR last_delivery_sequence > 0",
            name="last_delivery_sequence_positive",
        ),
        Index("ix_tasks_job_status", "job_id", "status"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ID,
        ForeignKey("jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_delivery_sequence: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checkpoint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class IngestionFingerprintTable(TimestampMixin, Base):
    __tablename__ = "ingestion_fingerprints"
    __table_args__ = (
        UniqueConstraint(
            "dataset_id",
            "file_sha256",
            "config_digest",
            name="uq_ingestion_fingerprints_dataset_file_config",
        ),
        Index("ix_ingestion_fingerprints_document", "document_id"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dataset_id: Mapped[str] = mapped_column(
        ID,
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    file_sha256: Mapped[str] = mapped_column(DIGEST, nullable=False)
    config_digest: Mapped[str] = mapped_column(DIGEST, nullable=False)
    document_id: Mapped[str] = mapped_column(
        ID,
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_id: Mapped[str] = mapped_column(
        ID,
        ForeignKey("jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False)


class OutboxEventTable(TimestampMixin, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_outbox_events_task_id"),
        CheckConstraint("attempt >= 0", name="attempt_non_negative"),
        Index("ix_outbox_events_status_created", "status", "created_at"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ID,
        ForeignKey("tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    staging_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)


class IndexBuildTable(TimestampMixin, Base):
    __tablename__ = "index_builds"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "index_version",
            name="uq_index_builds_document_version",
        ),
        CheckConstraint("index_version > 0", name="index_version_positive"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(
        ID,
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    index_version: Mapped[int] = mapped_column(Integer, nullable=False)
    job_id: Mapped[str] = mapped_column(
        ID,
        ForeignKey("jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class ChunkManifestTable(Base):
    __tablename__ = "chunk_manifests"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "index_version",
            "chunk_id",
            name="uq_chunk_manifests_document_version_chunk",
        ),
        CheckConstraint("index_version > 0", name="index_version_positive"),
        CheckConstraint("ordinal >= 0", name="ordinal_non_negative"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(
        ID,
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    index_version: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_id: Mapped[str] = mapped_column(VARCHAR(16, charset="ascii", collation="ascii_bin"))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(DIGEST, nullable=False)
    source_name: Mapped[str] = mapped_column(String(1024), nullable=False)
    locator: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )


class IdempotencyRecordTable(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "operation_type",
            "idempotency_key",
            name="uq_idempotency_records_operation_key",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dataset_id: Mapped[str] = mapped_column(
        ID,
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_digest: Mapped[str | None] = mapped_column(DIGEST, nullable=True)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )
