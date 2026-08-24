from __future__ import annotations

import pytest

from rag_mvp.domain.errors import DomainFailure
from tests.fakes.task_queue import FakeTaskQueue


@pytest.mark.asyncio
async def test_queue_preserves_at_least_once_delivery_and_explicit_ack_nak() -> None:
    queue = FakeTaskQueue()
    await queue.publish("task-1")

    first = await queue.consume("worker-a", timeout_seconds=0)
    assert first is not None
    assert first.task_id == "task-1"
    assert first.delivery_sequence == 1
    await queue.nak(first, delay_seconds=0, error=DomainFailure("TEMP", "retry", True))

    redelivery = await queue.consume("worker-a", timeout_seconds=0)
    assert redelivery is not None
    assert redelivery.task_id == "task-1"
    assert redelivery.delivery_sequence == 2
    assert redelivery.redelivery_count == 1
    await queue.ack(redelivery)

    assert await queue.consume("worker-a", timeout_seconds=0) is None
    assert queue.acked_task_ids == ["task-1"]


@pytest.mark.asyncio
async def test_unacked_delivery_can_be_redelivered() -> None:
    queue = FakeTaskQueue()
    await queue.publish("task-1")
    first = await queue.consume("worker-a", timeout_seconds=0)
    assert first is not None

    await queue.redeliver_unacked()
    second = await queue.consume("worker-b", timeout_seconds=0)

    assert second is not None
    assert second.delivery_sequence > first.delivery_sequence
    assert second.redelivery_count == 1
