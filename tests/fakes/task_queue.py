"""At-least-once task queue used only by tests."""

from __future__ import annotations

import asyncio
from collections import deque

from rag_mvp.domain.errors import DomainFailure
from rag_mvp.domain.ids import new_id
from rag_mvp.ports.message_queue import Delivery


class FakeTaskQueue:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._available: deque[Delivery] = deque()
        self._in_flight: dict[str, Delivery] = {}
        self._next_sequence = 1
        self.acked_task_ids: list[str] = []
        self.nak_failures: list[DomainFailure] = []

    def _delivery(self, task_id: str, redelivery_count: int) -> Delivery:
        delivery = Delivery(
            id=new_id(),
            task_id=task_id,
            delivery_sequence=self._next_sequence,
            redelivery_count=redelivery_count,
        )
        self._next_sequence += 1
        return delivery

    async def publish(self, task_id: str) -> None:
        async with self._lock:
            self._available.append(self._delivery(task_id, 0))

    async def consume(self, worker_id: str, timeout_seconds: float) -> Delivery | None:
        del worker_id, timeout_seconds
        async with self._lock:
            if not self._available:
                return None
            delivery = self._available.popleft()
            self._in_flight[delivery.id] = delivery
            return delivery

    async def ack(self, delivery: Delivery) -> None:
        async with self._lock:
            if self._in_flight.pop(delivery.id, None) is not None:
                self.acked_task_ids.append(delivery.task_id)

    async def nak(self, delivery: Delivery, delay_seconds: float, error: DomainFailure) -> None:
        del delay_seconds
        async with self._lock:
            current = self._in_flight.pop(delivery.id, None)
            if current is None:
                return
            self.nak_failures.append(error)
            self._available.append(self._delivery(current.task_id, current.redelivery_count + 1))

    async def redeliver_unacked(self) -> None:
        async with self._lock:
            in_flight = tuple(self._in_flight.values())
            self._in_flight.clear()
            for delivery in in_flight:
                self._available.append(
                    self._delivery(delivery.task_id, delivery.redelivery_count + 1)
                )
