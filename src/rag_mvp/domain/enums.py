"""Domain state and operation enumerations."""

from enum import StrEnum


class DocumentStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    FAILED = "FAILED"
    DELETED = "DELETED"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobType(StrEnum):
    INGEST_DOCUMENT = "INGEST_DOCUMENT"
    DELETE_DOCUMENT = "DELETE_DOCUMENT"
    CLEANUP_INDEX_VERSION = "CLEANUP_INDEX_VERSION"


class TaskType(StrEnum):
    INGEST_DOCUMENT = "INGEST_DOCUMENT"
    CLEANUP_DOCUMENT = "CLEANUP_DOCUMENT"
    CLEANUP_INDEX_VERSION = "CLEANUP_INDEX_VERSION"


class OutboxStatus(StrEnum):
    WAITING_OBJECT = "WAITING_OBJECT"
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    PUBLISHED = "PUBLISHED"
    CANCELLED = "CANCELLED"


class FingerprintState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    RELEASED = "RELEASED"


class IndexBuildStatus(StrEnum):
    BUILDING = "BUILDING"
    ACTIVE = "ACTIVE"
    ABANDONED = "ABANDONED"
