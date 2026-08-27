"""仅测试使用的内存对象存储。"""

from datetime import UTC, datetime

from rag_mvp.ports.storage import StoredObject


class FakeObjectStorage:
    def __init__(self) -> None:
        """初始化测试替身的内存状态。"""
        self.objects: dict[str, bytes] = {}
        self.modified_at: dict[str, datetime] = {}

    async def write(self, key: str, content: bytes) -> None:
        """将测试字节写入内存对象存储。"""
        self.objects[key] = bytes(content)
        self.modified_at[key] = datetime.now(UTC)

    async def read(self, key: str) -> bytes:
        """从内存对象存储读取指定对象。"""
        return self.objects[key]

    async def promote(self, staging_key: str, final_key: str) -> str:
        """模拟暂存对象原子提升到正式键。"""
        if final_key in self.objects:
            self.objects.pop(staging_key, None)
            self.modified_at.pop(staging_key, None)
            return final_key
        self.objects[final_key] = self.objects.pop(staging_key)
        self.modified_at[final_key] = self.modified_at.pop(staging_key)
        return final_key

    async def delete(self, key: str) -> None:
        """幂等删除内存对象。"""
        self.objects.pop(key, None)
        self.modified_at.pop(key, None)

    async def exists(self, key: str) -> bool:
        """检查内存对象是否存在。"""
        return key in self.objects

    async def list_objects(self, prefix: str) -> tuple[StoredObject, ...]:
        """按前缀列出内存对象，供暂存对象清理测试使用。"""
        return tuple(
            StoredObject(key, self.modified_at[key])
            for key in sorted(self.objects)
            if key.startswith(prefix)
        )
