"""仅测试使用的可推进确定性时钟。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(slots=True)
class FakeClock:
    current: datetime

    def now(self) -> datetime:
        """返回可控测试时钟的当前时间。"""
        return self.current

    def advance(self, delta: timedelta) -> None:
        """推进可控测试时钟，模拟时间流逝。"""
        self.current += delta
