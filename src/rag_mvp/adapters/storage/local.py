"""本地文件系统对象存储：用于开发环境验证 staging 提升与清理语义。"""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from rag_mvp.ports.storage import StoredObject


class LocalObjectStorage:
    # 确定对象存储根目录，并确保目录存在。
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    # 将对象键解析为根目录内的安全相对路径，拒绝路径穿越。
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

    # 通过同目录临时文件和原子替换写入对象。
    async def write(self, key: str, content: bytes) -> None:
        path = self._path(key)

        # 在阻塞线程中落盘、同步文件内容，再原子替换目标文件。
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

    # 在线程中读取指定对象的完整字节。
    async def read(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    # 将 staging 对象原子提升为正式对象；目标已存在时丢弃 staging 副本。
    async def promote(self, staging_key: str, final_key: str) -> str:
        staging_path = self._path(staging_key)
        final_path = self._path(final_key)

        # 在阻塞线程中完成目标目录创建和原子移动。
        def atomic_promote() -> None:
            final_path.parent.mkdir(parents=True, exist_ok=True)
            if final_path.exists():
                staging_path.unlink(missing_ok=True)
                return
            os.replace(staging_path, final_path)

        await asyncio.to_thread(atomic_promote)
        return final_key

    # 删除对象；对象不存在时视为清理完成。
    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, missing_ok=True)

    # 判断对象键是否对应一个普通文件。
    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)

    # 列出前缀目录下的文件及其最后修改时间。
    async def list_objects(self, prefix: str) -> tuple[StoredObject, ...]:
        prefix_path = self._path(prefix)

        # 在阻塞线程中递归扫描普通文件，并按对象键稳定排序。
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
