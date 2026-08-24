"""Dense and sparse search capability boundary."""

from typing import Protocol


class SearchEngine(Protocol):
    """Index and retrieve versioned chunks through Elasticsearch."""
