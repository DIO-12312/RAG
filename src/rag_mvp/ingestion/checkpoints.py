"""Named ingestion checkpoints used by resilience tests and worker instrumentation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum


class Checkpoint(StrEnum):
    AFTER_PARSE = "after_parse"
    AFTER_INDEX_WRITE = "after_index_write"
    AFTER_COMPLETE_BEFORE_ACK = "after_complete_before_ack"


class InjectedWorkerCrash(BaseException):
    """Simulate abrupt process death without being converted into a business failure."""

    def __init__(self, checkpoint: Checkpoint) -> None:
        super().__init__(f"injected worker crash at {checkpoint.value}")
        self.checkpoint = checkpoint


type Failpoint = Callable[[Checkpoint], Awaitable[None]]
