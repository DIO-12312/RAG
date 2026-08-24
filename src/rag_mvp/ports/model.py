"""Embedding and reranking model capability boundary."""

from typing import Protocol


class ModelGateway(Protocol):
    """Provide embedding and reranking without leaking a model SDK."""
