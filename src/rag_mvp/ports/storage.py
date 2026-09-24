"""对象存储能力边界：支持 staging 写入、正式提升与可重试物理删除。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    modified_at: datetime


class ObjectStorage(Protocol):
    """Store staging and final source objects."""

    # 将指定字节写入 key；调用方负责区分 staging 与正式路径。
    async def write(self, key: str, content: bytes) -> None: ...

    # 读取 key 对应对象的完整原始字节。
    async def read(self, key: str) -> bytes: ...

    # 将 staging 对象提升到正式 key，并返回最终对象 key。
    async def promote(self, staging_key: str, final_key: str) -> str: ...

    # 删除 key 对应的对象；重复删除必须安全。
    async def delete(self, key: str) -> None: ...

    # 判断 key 对应的对象当前是否存在。
    async def exists(self, key: str) -> bool: ...

    # 列出指定前缀下的对象及其最后修改时间，供 staging TTL 清理使用。
    async def list_objects(self, prefix: str) -> tuple[StoredObject, ...]: ...
