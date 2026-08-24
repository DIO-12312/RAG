"""Document chunker capability boundary."""

from typing import Protocol


class Chunker(Protocol):
    """Split normalized content into stable evidence chunks."""
