"""命名摄取检查点：为韧性测试和 Worker 可观测性提供稳定故障窗口。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum


class Checkpoint(StrEnum):
    AFTER_PARSE = "after_parse"
    AFTER_INDEX_WRITE = "after_index_write"
    AFTER_COMPLETE_BEFORE_ACK = "after_complete_before_ack"
    AFTER_RELAY_PUBLISH_BEFORE_MARK = "after_relay_publish_before_mark"


class InjectedWorkerCrash(BaseException):
    """Simulate abrupt process death without being converted into a business failure."""

    # 初始化该对象的依赖、配置或受控资源。
    def __init__(self, checkpoint: Checkpoint) -> None:
        super().__init__(f"injected worker crash at {checkpoint.value}")
        self.checkpoint = checkpoint


type Failpoint = Callable[[Checkpoint], Awaitable[None]]
