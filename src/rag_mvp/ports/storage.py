"""Object storage capability boundary."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    modified_at: datetime


class ObjectStorage(Protocol):
    """Store staging and final source objects."""

    async def write(self, key: str, content: bytes) -> None: ...

    async def read(self, key: str) -> bytes: ...

    async def promote(self, staging_key: str, final_key: str) -> str: ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...

    async def list_objects(self, prefix: str) -> tuple[StoredObject, ...]: ...
