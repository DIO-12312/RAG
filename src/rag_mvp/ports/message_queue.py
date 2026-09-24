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

    # 向 JetStream 发布 task_id，供 Worker 至少一次消费。
    async def publish(self, task_id: str) -> None: ...

    # 以 worker_id 拉取一条投递；超时无消息时返回 None。
    async def consume(self, worker_id: str, timeout_seconds: float) -> Delivery | None: ...

    # 确认投递已处理，使其不再被队列重投。
    async def ack(self, delivery: Delivery) -> None: ...

    # 否定确认投递，附带失败原因并按延迟请求重新投递。
    async def nak(self, delivery: Delivery, delay_seconds: float, error: DomainFailure) -> None: ...

    # 长耗时执行期间续约投递，避免 ack_wait 到期触发重复投递。
    async def in_progress(self, delivery: Delivery) -> None: ...
