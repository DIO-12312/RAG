"""Object storage capability boundary."""

from typing import Protocol


class ObjectStorage(Protocol):
    """Store staging and final source objects."""

    async def write(self, key: str, content: bytes) -> None: ...

    async def read(self, key: str) -> bytes: ...

    async def promote(self, staging_key: str, final_key: str) -> str: ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...
