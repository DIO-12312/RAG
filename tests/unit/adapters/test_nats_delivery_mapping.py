"""JetStream 消息元数据到领域 delivery 的映射单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from nats.aio.msg import Msg

from rag_mvp.adapters.message_queue.nats_jetstream import delivery_from_message
from rag_mvp.domain.errors import DomainError


def _message(
    data: bytes = b"task-1",
    *,
    consumer_sequence: int = 9,
    stream_sequence: int = 4,
    num_delivered: int = 3,
) -> Msg:
    """构造本测试所需的输入、替身或运行环境。"""
    metadata = Msg.Metadata(
        sequence=Msg.Metadata.SequencePair(
            consumer=consumer_sequence,
            stream=stream_sequence,
        ),
        num_pending=0,
        num_delivered=num_delivered,
        timestamp=datetime.now(UTC),
        stream="RAG_TASKS",
        consumer="rag-worker",
    )
    return Msg(cast(Any, object()), data=data, _metadata=metadata)


def test_delivery_uses_task_id_consumer_sequence_and_redelivery_count() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    message = _message()

    delivery = delivery_from_message(message)

    assert delivery.id == "RAG_TASKS:rag-worker:4"
    assert delivery.task_id == "task-1"
    assert delivery.delivery_sequence == message.metadata.sequence.consumer == 9
    assert delivery.redelivery_count == message.metadata.num_delivered - 1 == 2


@pytest.mark.parametrize(
    "message",
    [
        _message(b""),
        _message(b"\xff"),
        _message(consumer_sequence=0),
        _message(stream_sequence=0),
        _message(num_delivered=0),
    ],
)
def test_delivery_rejects_invalid_payload_and_metadata(message: Msg) -> None:
    """验证本测试场景的预期行为与边界条件。"""
    with pytest.raises(DomainError) as error:
        delivery_from_message(message)

    assert error.value.failure.code == "QUEUE_MESSAGE_INVALID"
    assert error.value.failure.retryable is False
