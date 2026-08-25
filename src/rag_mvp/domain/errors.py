"""Stable domain failures independent of transport and infrastructure SDKs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DomainFailure:
    code: str
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("failure code must not be empty")


class DomainError(Exception):
    """Base exception carrying a stable machine-readable failure."""

    def __init__(self, failure: DomainFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


class InvalidStateTransition(DomainError):
    """Raised when a state machine transition violates a terminal fence."""

    def __init__(self, current: object, target: object) -> None:
        super().__init__(
            DomainFailure(
                code="INVALID_STATE_TRANSITION",
                message=f"cannot transition from {current} to {target}",
            )
        )
