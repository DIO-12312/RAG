"""In-memory object storage used only by tests."""


class FakeObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def write(self, key: str, content: bytes) -> None:
        self.objects[key] = bytes(content)

    async def read(self, key: str) -> bytes:
        return self.objects[key]

    async def promote(self, staging_key: str, final_key: str) -> str:
        if final_key in self.objects:
            self.objects.pop(staging_key, None)
            return final_key
        self.objects[final_key] = self.objects.pop(staging_key)
        return final_key

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self.objects
