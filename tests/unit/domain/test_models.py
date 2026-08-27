from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rag_mvp.domain.enums import (
    DatasetStatus,
    DocumentStatus,
    JobStatus,
    JobType,
    TaskStatus,
    TaskType,
)
from rag_mvp.domain.models import Dataset, Document, Job, Task


def test_dataset_requires_embedding_dimension_and_model() -> None:
    with pytest.raises(ValueError, match="embedding_dimension"):
        Dataset(
            id="dataset-1",
            name="Docs",
            embedding_model="model",
            embedding_dimension=0,
            created_at=datetime.now(UTC),
        )


def test_dataset_preserves_tenant_boundary() -> None:
    dataset = Dataset(
        id="dataset-1",
        name="Docs",
        embedding_model="model",
        embedding_dimension=1024,
        created_at=datetime.now(UTC),
        tenant_id="default_tenant",
    )

    assert dataset.tenant_id == "default_tenant"


def test_dataset_starts_active_with_a_non_negative_lifecycle_generation() -> None:
    dataset = Dataset(
        id="dataset-1",
        name="Docs",
        embedding_model="model",
        embedding_dimension=1024,
        created_at=datetime.now(UTC),
        status=DatasetStatus.ACTIVE,
        lifecycle_generation=0,
    )

    assert dataset.status is DatasetStatus.ACTIVE
    assert dataset.lifecycle_generation == 0

    with pytest.raises(ValueError, match="lifecycle_generation"):
        Dataset(
            id="dataset-1",
            name="Docs",
            embedding_model="model",
            embedding_dimension=1024,
            created_at=datetime.now(UTC),
            status=DatasetStatus.DELETING,
            lifecycle_generation=-1,
        )


def test_dataset_cleanup_job_has_dataset_scope_but_no_document() -> None:
    job = Job(
        id="job-1",
        type=JobType.DELETE_DATASET,
        dataset_id="dataset-1",
        document_id=None,
        config_digest="b" * 64,
        index_version=1,
        document_generation=1,
        status=JobStatus.PENDING,
        progress=0.0,
        created_at=datetime.now(UTC),
    )

    assert job.dataset_id == "dataset-1"
    assert job.document_id is None


def test_document_versions_and_generation_are_non_negative() -> None:
    with pytest.raises(ValueError, match="next_index_version"):
        Document(
            id="doc-1",
            dataset_id="dataset-1",
            source_name="doc.txt",
            file_sha256="a" * 64,
            status=DocumentStatus.PENDING,
            active_version=None,
            next_index_version=0,
            lifecycle_generation=0,
            created_at=datetime.now(UTC),
        )


def test_job_progress_is_normalized() -> None:
    with pytest.raises(ValueError, match="progress"):
        Job(
            id="job-1",
            type=JobType.INGEST_DOCUMENT,
            document_id="doc-1",
            config_digest="b" * 64,
            index_version=1,
            document_generation=0,
            status=JobStatus.PENDING,
            progress=1.1,
            created_at=datetime.now(UTC),
        )


def test_task_delivery_counters_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="attempt"):
        Task(
            id="task-1",
            job_id="job-1",
            type=TaskType.INGEST_DOCUMENT,
            status=TaskStatus.PENDING,
            attempt=-1,
            last_delivery_sequence=None,
            checkpoint=None,
            created_at=datetime.now(UTC),
        )
