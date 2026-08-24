"""Durable task queue capability boundary."""

from typing import Protocol


class TaskQueue(Protocol):
    """Publish and consume task identifiers through NATS JetStream."""
