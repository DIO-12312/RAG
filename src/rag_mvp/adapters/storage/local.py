"""Filesystem-backed source object storage."""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from rag_mvp.ports.storage import StoredObject


class LocalObjectStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

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

    async def write(self, key: str, content: bytes) -> None:
        path = self._path(key)

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

    async def read(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def promote(self, staging_key: str, final_key: str) -> str:
        staging_path = self._path(staging_key)
        final_path = self._path(final_key)

        def atomic_promote() -> None:
            final_path.parent.mkdir(parents=True, exist_ok=True)
            if final_path.exists():
                staging_path.unlink(missing_ok=True)
                return
            os.replace(staging_path, final_path)

        await asyncio.to_thread(atomic_promote)
        return final_key

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, missing_ok=True)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)

    async def list_objects(self, prefix: str) -> tuple[StoredObject, ...]:
        prefix_path = self._path(prefix)

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
