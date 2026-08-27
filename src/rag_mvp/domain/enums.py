"""领域状态与操作枚举，是 Job、Task、Document 状态机的有限集合。"""

from enum import StrEnum


class DatasetStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DELETING = "DELETING"


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
    DELETE_DATASET = "DELETE_DATASET"


class TaskType(StrEnum):
    INGEST_DOCUMENT = "INGEST_DOCUMENT"
    CLEANUP_DOCUMENT = "CLEANUP_DOCUMENT"
    CLEANUP_INDEX_VERSION = "CLEANUP_INDEX_VERSION"
    CLEANUP_DATASET = "CLEANUP_DATASET"


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
