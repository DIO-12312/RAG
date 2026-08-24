"""Metadata persistence capability boundary."""

from typing import Protocol


class MetadataRepository(Protocol):
    """Persist authoritative RAG metadata and state transitions.

    Transactional methods are added with the final Milestone B schema so this
    baseline cannot accidentally establish partial Task/Outbox semantics.
    """
