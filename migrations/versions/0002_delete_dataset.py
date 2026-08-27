"""Add dataset lifecycle and dataset-scoped work ownership.

Revision ID: 0002_delete_dataset
Revises: 0001_core_schema
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0002_delete_dataset"
down_revision: str | None = "0001_core_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id() -> mysql.VARCHAR:
    return mysql.VARCHAR(36, charset="ascii", collation="ascii_bin")


def upgrade() -> None:
    op.add_column(
        "datasets",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
    )
    op.add_column(
        "datasets",
        sa.Column("lifecycle_generation", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_datasets_lifecycle_generation_non_negative",
        "datasets",
        "lifecycle_generation >= 0",
    )
    op.create_index("ix_datasets_tenant_status", "datasets", ["tenant_id", "status"], unique=False)

    op.add_column("jobs", sa.Column("dataset_id", _id(), nullable=True))
    op.execute(
        "UPDATE jobs JOIN documents ON documents.id = jobs.document_id "
        "SET jobs.dataset_id = documents.dataset_id"
    )
    op.alter_column("jobs", "dataset_id", existing_type=_id(), nullable=False)
    op.alter_column("jobs", "document_id", existing_type=_id(), nullable=True)
    op.create_foreign_key(
        "fk_jobs_dataset_id_datasets",
        "jobs",
        "datasets",
        ["dataset_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_jobs_dataset_status", "jobs", ["dataset_id", "status"], unique=False)

    op.add_column("idempotency_records", sa.Column("dataset_id", _id(), nullable=True))
    op.execute(
        "UPDATE idempotency_records r "
        "JOIN documents d ON d.id = JSON_UNQUOTE(JSON_EXTRACT(r.result_json, '$.document_id')) "
        "SET r.dataset_id = d.dataset_id WHERE r.dataset_id IS NULL"
    )
    op.execute(
        "UPDATE idempotency_records r "
        "JOIN jobs j ON j.id = JSON_UNQUOTE(JSON_EXTRACT(r.result_json, '$.job_id')) "
        "SET r.dataset_id = j.dataset_id WHERE r.dataset_id IS NULL"
    )
    op.execute(
        "UPDATE idempotency_records r "
        "JOIN datasets d ON d.id = JSON_UNQUOTE(JSON_EXTRACT(r.result_json, '$.dataset_id')) "
        "SET r.dataset_id = d.id WHERE r.dataset_id IS NULL"
    )
    op.alter_column("idempotency_records", "dataset_id", existing_type=_id(), nullable=False)
    op.create_foreign_key(
        "fk_idempotency_records_dataset_id_datasets",
        "idempotency_records",
        "datasets",
        ["dataset_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_idempotency_records_dataset_id",
        "idempotency_records",
        ["dataset_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_idempotency_records_dataset_id", table_name="idempotency_records")
    op.drop_constraint(
        "fk_idempotency_records_dataset_id_datasets",
        "idempotency_records",
        type_="foreignkey",
    )
    op.drop_column("idempotency_records", "dataset_id")

    op.drop_index("ix_jobs_dataset_status", table_name="jobs")
    op.drop_constraint("fk_jobs_dataset_id_datasets", "jobs", type_="foreignkey")
    op.alter_column("jobs", "document_id", existing_type=_id(), nullable=False)
    op.drop_column("jobs", "dataset_id")

    op.drop_index("ix_datasets_tenant_status", table_name="datasets")
    op.drop_constraint("ck_datasets_lifecycle_generation_non_negative", "datasets", type_="check")
    op.drop_column("datasets", "lifecycle_generation")
    op.drop_column("datasets", "status")
