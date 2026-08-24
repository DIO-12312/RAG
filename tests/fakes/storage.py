"""In-memory object storage used only by tests."""

from datetime import UTC, datetime

from rag_mvp.ports.storage import StoredObject


class FakeObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.modified_at: dict[str, datetime] = {}

    async def write(self, key: str, content: bytes) -> None:
        self.objects[key] = bytes(content)
        self.modified_at[key] = datetime.now(UTC)

    async def read(self, key: str) -> bytes:
        return self.objects[key]

    async def promote(self, staging_key: str, final_key: str) -> str:
        if final_key in self.objects:
            self.objects.pop(staging_key, None)
            self.modified_at.pop(staging_key, None)
            return final_key
        self.objects[final_key] = self.objects.pop(staging_key)
        self.modified_at[final_key] = self.modified_at.pop(staging_key)
        return final_key

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.modified_at.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self.objects

    async def list_objects(self, prefix: str) -> tuple[StoredObject, ...]:
        return tuple(
            StoredObject(key, self.modified_at[key])
            for key in sorted(self.objects)
            if key.startswith(prefix)
        )
