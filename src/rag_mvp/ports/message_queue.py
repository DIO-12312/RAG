"""持久任务队列能力边界：抽象发布、拉取及显式确认，保留投递语义。"""

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

    # 实现 publish 对应的局部职责。
    async def publish(self, task_id: str) -> None: ...

    # 实现 consume 对应的局部职责。
    async def consume(self, worker_id: str, timeout_seconds: float) -> Delivery | None: ...

    # 实现 ack 对应的局部职责。
    async def ack(self, delivery: Delivery) -> None: ...

    # 实现 nak 对应的局部职责。
    async def nak(self, delivery: Delivery, delay_seconds: float, error: DomainFailure) -> None: ...
