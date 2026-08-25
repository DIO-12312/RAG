"""Durable task queue capability boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from rag_mvp.domain.errors import DomainFailure


@dataclass(frozen=True, slots=True)
class Delivery:
    id: str
    task_id: str
    delivery_sequence: int
    redelivery_count: int


class TaskQueue(Protocol):
    """Publish and consume task identifiers through NATS JetStream."""

    async def publish(self, task_id: str) -> None: ...

    async def consume(self, worker_id: str, timeout_seconds: float) -> Delivery | None: ...

    async def ack(self, delivery: Delivery) -> None: ...

    async def nak(self, delivery: Delivery, delay_seconds: float, error: DomainFailure) -> None: ...
