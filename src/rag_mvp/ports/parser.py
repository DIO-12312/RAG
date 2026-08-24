"""Document parser capability boundary."""

from typing import Protocol


class Parser(Protocol):
    """Parse source objects into normalized, traceable content."""
