"""NATS JetStream 持久任务队列实现，保留显式 ACK/NAK 与重投语义。"""

from __future__ import annotations

import math

import nats
from nats.aio.client import Client as NATS
from nats.aio.msg import Msg
from nats.errors import Error as NatsError
from nats.errors import NotJSMessageError
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)
from nats.js.client import JetStreamContext
from nats.js.errors import FetchTimeoutError, NotFoundError

from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.ports.message_queue import Delivery


# 实现 delivery_from_message 对应的局部职责。
def delivery_from_message(message: Msg) -> Delivery:
    """Map JetStream metadata to the queue's infrastructure-neutral delivery."""

    try:
        task_id = message.data.decode("utf-8").strip()
        metadata = message.metadata
    except (UnicodeDecodeError, NotJSMessageError) as exc:
        raise _invalid_message() from exc
    if not task_id:
        raise _invalid_message()
    if metadata.sequence.consumer < 1 or metadata.sequence.stream < 1 or metadata.num_delivered < 1:
        raise _invalid_message()
    return Delivery(
        id=f"{metadata.stream}:{metadata.consumer}:{metadata.sequence.stream}",
        task_id=task_id,
        delivery_sequence=metadata.sequence.consumer,
        redelivery_count=metadata.num_delivered - 1,
    )


class NatsJetStreamTaskQueue:
    """Publish task IDs and pull explicit-ack deliveries from one durable consumer."""

    # 初始化该对象的依赖、配置或受控资源。
    def __init__(
        self,
        connection: NATS,
        jetstream: JetStreamContext,
        subscription: JetStreamContext.PullSubscription,
        stream: str,
        subject: str,
    ) -> None:
        self._connection = connection
        self._jetstream = jetstream
        self._subscription = subscription
        self._stream = stream
        self._subject = subject
        self._in_flight: dict[str, Msg] = {}

    @classmethod
    # 建立连接并幂等准备运行所需的基础设施。
    async def connect(
        cls,
        url: str,
        stream: str,
        subject: str,
        consumer: str,
        ack_wait_seconds: float,
        max_deliver: int,
    ) -> NatsJetStreamTaskQueue:
        """Connect and idempotently provision a file-backed work queue."""

        cls._validate_configuration(
            url,
            stream,
            subject,
            consumer,
            ack_wait_seconds,
            max_deliver,
        )
        connection: NATS | None = None
        try:
            connection = await nats.connect(url, connect_timeout=5)
            jetstream = connection.jetstream()
            await cls._ensure_stream(jetstream, stream, subject)
            consumer_config = ConsumerConfig(
                durable_name=consumer,
                ack_policy=AckPolicy.EXPLICIT,
                ack_wait=ack_wait_seconds,
                max_deliver=max_deliver,
                filter_subject=subject,
            )
            await cls._ensure_consumer(jetstream, stream, consumer, consumer_config)
            subscription = await jetstream.pull_subscribe(
                subject,
                durable=consumer,
                stream=stream,
            )
            return cls(connection, jetstream, subscription, stream, subject)
        except DomainError:
            if connection is not None:
                await connection.close()
            raise
        except NatsError as exc:
            if connection is not None:
                await connection.close()
            raise cls._unavailable("task queue could not be connected or provisioned") from exc

    # 实现 publish 对应的局部职责。
    async def publish(self, task_id: str) -> None:
        if not task_id.strip():
            raise ValueError("task_id must not be empty")
        try:
            await self._jetstream.publish(
                self._subject,
                task_id.encode("utf-8"),
                stream=self._stream,
            )
        except NatsError as exc:
            raise self._unavailable("task could not be published") from exc

    # 实现 consume 对应的局部职责。
    async def consume(self, worker_id: str, timeout_seconds: float) -> Delivery | None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must not be negative")
        try:
            messages = await self._subscription.fetch(
                batch=1,
                timeout=max(timeout_seconds, 0.001),
            )
        except (FetchTimeoutError, NatsTimeoutError):
            return None
        except NatsError as exc:
            raise self._unavailable("task delivery could not be fetched") from exc
        if not messages:
            return None
        message = messages[0]
        delivery = delivery_from_message(message)
        self._in_flight[delivery.id] = message
        return delivery

    # 实现 ack 对应的局部职责。
    async def ack(self, delivery: Delivery) -> None:
        message = self._in_flight.get(delivery.id)
        if message is None:
            return
        try:
            await message.ack()
        except NatsError as exc:
            raise self._unavailable("task delivery could not be acknowledged") from exc
        self._in_flight.pop(delivery.id, None)

    # 实现 nak 对应的局部职责。
    async def nak(
        self,
        delivery: Delivery,
        delay_seconds: float,
        error: DomainFailure,
    ) -> None:
        del error
        if delay_seconds < 0:
            raise ValueError("delay_seconds must not be negative")
        message = self._in_flight.get(delivery.id)
        if message is None:
            return
        try:
            await message.nak(delay=delay_seconds)
        except NatsError as exc:
            raise self._unavailable("task delivery could not be negatively acknowledged") from exc
        self._in_flight.pop(delivery.id, None)

    # 按资源所有权顺序关闭底层连接或句柄。
    async def close(self) -> None:
        self._in_flight.clear()
        if not self._connection.is_closed:
            await self._connection.close()

    @staticmethod
    # 内部辅助：完成 ensure_stream 所需的局部转换或校验。
    async def _ensure_stream(
        jetstream: JetStreamContext,
        stream: str,
        subject: str,
    ) -> None:
        try:
            info = await jetstream.stream_info(stream)
        except NotFoundError:
            await jetstream.add_stream(
                config=StreamConfig(
                    name=stream,
                    subjects=[subject],
                    retention=RetentionPolicy.WORK_QUEUE,
                    storage=StorageType.FILE,
                )
            )
            return
        subjects = set(info.config.subjects or [])
        if (
            subjects != {subject}
            or info.config.retention != RetentionPolicy.WORK_QUEUE
            or info.config.storage != StorageType.FILE
        ):
            raise _configuration_mismatch("existing stream configuration is incompatible")

    @staticmethod
    # 内部辅助：完成 ensure_consumer 所需的局部转换或校验。
    async def _ensure_consumer(
        jetstream: JetStreamContext,
        stream: str,
        consumer: str,
        expected: ConsumerConfig,
    ) -> None:
        try:
            info = await jetstream.consumer_info(stream, consumer)
        except NotFoundError:
            await jetstream.add_consumer(stream, config=expected)
            return
        actual = info.config
        if (
            actual.durable_name != expected.durable_name
            or actual.ack_policy != AckPolicy.EXPLICIT
            or actual.filter_subject != expected.filter_subject
            or actual.max_deliver != expected.max_deliver
            or actual.ack_wait is None
            or expected.ack_wait is None
            or not math.isclose(actual.ack_wait, expected.ack_wait, rel_tol=0.0, abs_tol=0.001)
        ):
            raise _configuration_mismatch("existing consumer configuration is incompatible")

    @staticmethod
    # 内部辅助：完成 validate_configuration 所需的局部转换或校验。
    def _validate_configuration(
        url: str,
        stream: str,
        subject: str,
        consumer: str,
        ack_wait_seconds: float,
        max_deliver: int,
    ) -> None:
        if not all(value.strip() for value in (url, stream, subject, consumer)):
            raise ValueError("NATS URL, stream, subject, and consumer must not be empty")
        if ack_wait_seconds <= 0:
            raise ValueError("ack_wait_seconds must be positive")
        if max_deliver < 1:
            raise ValueError("max_deliver must be at least 1")

    @staticmethod
    # 内部辅助：完成 unavailable 所需的局部转换或校验。
    def _unavailable(message: str) -> DomainError:
        return DomainError(DomainFailure("QUEUE_UNAVAILABLE", message, retryable=True))


# 内部辅助：完成 invalid_message 所需的局部转换或校验。
def _invalid_message() -> DomainError:
    return DomainError(
        DomainFailure(
            "QUEUE_MESSAGE_INVALID",
            "JetStream message does not contain valid task delivery metadata",
            retryable=False,
        )
    )


# 内部辅助：完成 configuration_mismatch 所需的局部转换或校验。
def _configuration_mismatch(message: str) -> DomainError:
    return DomainError(DomainFailure("QUEUE_CONFIG_MISMATCH", message, retryable=False))
