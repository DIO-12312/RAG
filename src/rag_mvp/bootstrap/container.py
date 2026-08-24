"""The sole composition root for concrete runtime dependencies."""

from __future__ import annotations

import asyncio
import signal
from dataclasses import dataclass, field
from types import FrameType

from rag_mvp.config import Settings
from rag_mvp.rpc.rag_service import RagService


@dataclass(slots=True)
class Container:
    """Milestone A container with an idempotent lifecycle.

    Concrete adapters are intentionally absent until their contract tests and
    Milestone B domain schema exist.
    """

    settings: Settings
    rag_service: RagService | None = None
    _closed: bool = field(default=False, init=False)
    _close_count: int = field(default=0, init=False)

    @property
    def closed(self) -> bool:
        """Return whether resources have already been closed."""

        return self._closed

    @property
    def close_count(self) -> int:
        """Expose the number of real close operations for lifecycle tests."""

        return self._close_count

    async def close(self) -> None:
        """Close container resources exactly once."""

        if self._closed:
            return
        self._closed = True
        self._close_count += 1


def build_container(settings: Settings) -> Container:
    """Build dependencies explicitly without making external connections."""

    return Container(settings=settings)


def install_shutdown_handlers(stop_event: asyncio.Event) -> None:
    """Set *stop_event* for SIGINT/SIGTERM on Unix and Windows event loops."""

    loop = asyncio.get_running_loop()

    def request_stop(_signum: int | None = None, _frame: FrameType | None = None) -> None:
        loop.call_soon_threadsafe(stop_event.set)

    for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(shutdown_signal, stop_event.set)
        except NotImplementedError:
            signal.signal(shutdown_signal, request_stop)
