from __future__ import annotations

import pytest

from rag_mvp.domain.enums import (
    DocumentStatus,
    FingerprintState,
    IndexBuildStatus,
    JobStatus,
    OutboxStatus,
    TaskStatus,
)
from rag_mvp.domain.errors import InvalidStateTransition
from rag_mvp.domain.policies import (
    transition_document,
    transition_fingerprint,
    transition_index_build,
    transition_job,
    transition_outbox,
    transition_task,
)


@pytest.mark.parametrize(
    ("transition", "initial", "target"),
    [
        (transition_job, JobStatus.PENDING, JobStatus.RUNNING),
        (transition_job, JobStatus.PENDING, JobStatus.FAILED),
        (transition_task, TaskStatus.RUNNING, TaskStatus.SUCCEEDED),
        (transition_document, DocumentStatus.PENDING, DocumentStatus.READY),
        (transition_outbox, OutboxStatus.WAITING_OBJECT, OutboxStatus.READY_TO_PUBLISH),
        (transition_fingerprint, FingerprintState.RUNNING, FingerprintState.SUCCEEDED),
        (transition_index_build, IndexBuildStatus.BUILDING, IndexBuildStatus.ACTIVE),
    ],
)
def test_valid_state_transitions(transition: object, initial: object, target: object) -> None:
    assert transition(initial, target) is target  # type: ignore[operator]


@pytest.mark.parametrize(
    ("transition", "initial", "target"),
    [
        (transition_job, JobStatus.FAILED, JobStatus.PENDING),
        (transition_job, JobStatus.SUCCEEDED, JobStatus.RUNNING),
        (transition_task, TaskStatus.CANCELLED, TaskStatus.RUNNING),
        (transition_document, DocumentStatus.DELETED, DocumentStatus.READY),
        (transition_outbox, OutboxStatus.PUBLISHED, OutboxStatus.READY_TO_PUBLISH),
        (transition_fingerprint, FingerprintState.RELEASED, FingerprintState.RUNNING),
        (transition_index_build, IndexBuildStatus.ACTIVE, IndexBuildStatus.BUILDING),
    ],
)
def test_terminal_states_cannot_be_reopened(
    transition: object, initial: object, target: object
) -> None:
    with pytest.raises(InvalidStateTransition):
        transition(initial, target)  # type: ignore[operator]
