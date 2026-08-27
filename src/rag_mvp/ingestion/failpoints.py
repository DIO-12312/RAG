"""仅测试使用的跨进程文件屏障，用于构造确定性的崩溃时间窗口。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Self

from rag_mvp.config import Environment
from rag_mvp.ingestion.checkpoints import Checkpoint

if TYPE_CHECKING:
    from rag_mvp.config import Settings


class FileBarrierFailpoint:
    """Block once per shared root until a test creates the release marker."""

    # 初始化该对象的依赖、配置或受控资源。
    def __init__(
        self,
        root: Path,
        enabled_checkpoints: set[Checkpoint] | frozenset[Checkpoint],
        *,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self._root = root
        self._enabled = frozenset(enabled_checkpoints)
        self._poll_interval_seconds = poll_interval_seconds

    @classmethod
    # 实现 from_settings 对应的局部职责。
    def from_settings(cls, settings: Settings) -> Self | None:
        """Build only from an explicitly test-scoped Settings capability."""

        names = settings.failpoint_checkpoint_names
        root = settings.failpoint_root
        if root is None and not names:
            return None
        if settings.environment is not Environment.TEST:
            raise RuntimeError("file barrier failpoints require test environment")
        if root is None or not names:
            raise RuntimeError("failpoint root and checkpoints must be configured together")
        return cls(root, {Checkpoint(name) for name in names})

    # 作为可注入回调执行对应检查点行为。
    async def __call__(self, checkpoint: Checkpoint) -> None:
        if checkpoint not in self._enabled:
            return
        self._root.mkdir(parents=True, exist_ok=True)
        reached = self._root / f"{checkpoint.value}.reached"
        release = self._root / f"{checkpoint.value}.release"
        try:
            reached.touch(exist_ok=False)
        except FileExistsError:
            return
        while not release.exists():  # noqa: ASYNC110 - the barrier is an external file signal
            await asyncio.sleep(self._poll_interval_seconds)
