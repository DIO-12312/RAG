"""本地文件系统对象存储：用于开发环境验证 staging 提升与清理语义。"""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from rag_mvp.ports.storage import StoredObject


class LocalObjectStorage:
    # 初始化该对象的依赖、配置或受控资源。
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    # 内部辅助：完成 path 所需的局部转换或校验。
    def _path(self, key: str) -> Path:
        normalized = Path(key.replace("\\", "/"))
        if normalized.is_absolute() or ".." in normalized.parts or not normalized.parts:
            raise ValueError("object key must be a safe relative path")
        candidate = (self._root / normalized).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as error:
            raise ValueError("object key escapes storage root") from error
        return candidate

    # 实现 write 对应的局部职责。
    async def write(self, key: str, content: bytes) -> None:
        path = self._path(key)

        # 实现 atomic_write 对应的局部职责。
        def atomic_write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=".upload-")
            try:
                with os.fdopen(descriptor, "wb") as temporary:
                    temporary.write(content)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                os.replace(temporary_name, path)
            except BaseException:
                Path(temporary_name).unlink(missing_ok=True)
                raise

        await asyncio.to_thread(atomic_write)

    # 实现 read 对应的局部职责。
    async def read(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    # 实现 promote 对应的局部职责。
    async def promote(self, staging_key: str, final_key: str) -> str:
        staging_path = self._path(staging_key)
        final_path = self._path(final_key)

        # 实现 atomic_promote 对应的局部职责。
        def atomic_promote() -> None:
            final_path.parent.mkdir(parents=True, exist_ok=True)
            if final_path.exists():
                staging_path.unlink(missing_ok=True)
                return
            os.replace(staging_path, final_path)

        await asyncio.to_thread(atomic_promote)
        return final_key

    # 实现 delete 对应的局部职责。
    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, missing_ok=True)

    # 实现 exists 对应的局部职责。
    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)

    # 列出该方法负责的领域数据或基础设施状态。
    async def list_objects(self, prefix: str) -> tuple[StoredObject, ...]:
        prefix_path = self._path(prefix)

        # 实现 scan 对应的局部职责。
        def scan() -> tuple[StoredObject, ...]:
            if not prefix_path.exists():
                return ()
            return tuple(
                StoredObject(
                    path.relative_to(self._root).as_posix(),
                    datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
                )
                for path in sorted(prefix_path.rglob("*"))
                if path.is_file()
            )

        return await asyncio.to_thread(scan)
