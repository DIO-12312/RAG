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

    # 实现 write 对应的局部职责。
    async def write(self, key: str, content: bytes) -> None: ...

    # 实现 read 对应的局部职责。
    async def read(self, key: str) -> bytes: ...

    # 实现 promote 对应的局部职责。
    async def promote(self, staging_key: str, final_key: str) -> str: ...

    # 实现 delete 对应的局部职责。
    async def delete(self, key: str) -> None: ...

    # 实现 exists 对应的局部职责。
    async def exists(self, key: str) -> bool: ...

    # 列出该方法负责的领域数据或基础设施状态。
    async def list_objects(self, prefix: str) -> tuple[StoredObject, ...]: ...
