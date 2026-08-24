"""Object storage capability boundary."""

from typing import Protocol


class ObjectStorage(Protocol):
    """Store staging and final source objects."""
