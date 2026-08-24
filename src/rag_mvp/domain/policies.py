"""Pure state transition policies."""

from __future__ import annotations

from collections.abc import Mapping, Set
from typing import TypeVar

from rag_mvp.domain.enums import (
    DocumentStatus,
    FingerprintState,
    IndexBuildStatus,
    JobStatus,
    OutboxStatus,
    TaskStatus,
)
from rag_mvp.domain.errors import InvalidStateTransition

StatusT = TypeVar("StatusT")


def _transition(
    current: StatusT, target: StatusT, allowed: Mapping[StatusT, Set[StatusT]]
) -> StatusT:
    if target not in allowed.get(current, frozenset()):
        raise InvalidStateTransition(current, target)
    return target


_JOB_TRANSITIONS: Mapping[JobStatus, Set[JobStatus]] = {
    JobStatus.PENDING: frozenset({JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}),
}
_TASK_TRANSITIONS: Mapping[TaskStatus, Set[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED}),
    TaskStatus.RUNNING: frozenset({TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}),
}
_DOCUMENT_TRANSITIONS: Mapping[DocumentStatus, Set[DocumentStatus]] = {
    DocumentStatus.PENDING: frozenset(
        {DocumentStatus.READY, DocumentStatus.FAILED, DocumentStatus.DELETED}
    ),
    DocumentStatus.READY: frozenset({DocumentStatus.DELETED}),
    DocumentStatus.FAILED: frozenset({DocumentStatus.DELETED}),
}
_OUTBOX_TRANSITIONS: Mapping[OutboxStatus, Set[OutboxStatus]] = {
    OutboxStatus.WAITING_OBJECT: frozenset({OutboxStatus.READY_TO_PUBLISH, OutboxStatus.CANCELLED}),
    OutboxStatus.READY_TO_PUBLISH: frozenset({OutboxStatus.PUBLISHED, OutboxStatus.CANCELLED}),
}
_FINGERPRINT_TRANSITIONS: Mapping[FingerprintState, Set[FingerprintState]] = {
    FingerprintState.PENDING: frozenset(
        {
            FingerprintState.RUNNING,
            FingerprintState.SUCCEEDED,
            FingerprintState.FAILED_RETRYABLE,
            FingerprintState.RELEASED,
        }
    ),
    FingerprintState.RUNNING: frozenset(
        {
            FingerprintState.SUCCEEDED,
            FingerprintState.FAILED_RETRYABLE,
            FingerprintState.RELEASED,
        }
    ),
    FingerprintState.SUCCEEDED: frozenset({FingerprintState.RELEASED}),
    FingerprintState.FAILED_RETRYABLE: frozenset({FingerprintState.RELEASED}),
}
_INDEX_BUILD_TRANSITIONS: Mapping[IndexBuildStatus, Set[IndexBuildStatus]] = {
    IndexBuildStatus.BUILDING: frozenset({IndexBuildStatus.ACTIVE, IndexBuildStatus.ABANDONED})
}


def transition_job(current: JobStatus, target: JobStatus) -> JobStatus:
    return _transition(current, target, _JOB_TRANSITIONS)


def transition_task(current: TaskStatus, target: TaskStatus) -> TaskStatus:
    return _transition(current, target, _TASK_TRANSITIONS)


def transition_document(current: DocumentStatus, target: DocumentStatus) -> DocumentStatus:
    return _transition(current, target, _DOCUMENT_TRANSITIONS)


def transition_outbox(current: OutboxStatus, target: OutboxStatus) -> OutboxStatus:
    return _transition(current, target, _OUTBOX_TRANSITIONS)


def transition_fingerprint(current: FingerprintState, target: FingerprintState) -> FingerprintState:
    return _transition(current, target, _FINGERPRINT_TRANSITIONS)


def transition_index_build(current: IndexBuildStatus, target: IndexBuildStatus) -> IndexBuildStatus:
    return _transition(current, target, _INDEX_BUILD_TRANSITIONS)
