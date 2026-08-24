"""Safety and coordination rules for test-only file barrier failpoints."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from rag_mvp.config import Environment, Settings
from rag_mvp.ingestion.checkpoints import Checkpoint
from rag_mvp.ingestion.failpoints import FileBarrierFailpoint


@pytest.mark.parametrize("environment", [Environment.DEVELOPMENT, Environment.PRODUCTION])
def test_non_test_environment_rejects_configured_fault_injection(
    tmp_path: Path,
    environment: Environment,
) -> None:
    with pytest.raises(ValidationError, match="failpoints are allowed only in test"):
        Settings(
            environment=environment,
            grpc_reflection=False,
            mysql_dsn="mysql+asyncmy://rag:secret@mysql:3306/rag",
            failpoint_root=tmp_path,
            failpoint_checkpoints=Checkpoint.AFTER_INDEX_WRITE.value,
        )


def test_factory_defends_against_unconfigured_or_unvalidated_settings(tmp_path: Path) -> None:
    assert FileBarrierFailpoint.from_settings(Settings(_env_file=None)) is None
    unsafe = Settings.model_construct(
        environment=Environment.PRODUCTION,
        failpoint_root=tmp_path,
        failpoint_checkpoints=Checkpoint.AFTER_PARSE.value,
    )
    with pytest.raises(RuntimeError, match="require test environment"):
        FileBarrierFailpoint.from_settings(unsafe)
    partial = Settings.model_construct(
        environment=Environment.TEST,
        failpoint_root=tmp_path,
        failpoint_checkpoints="",
    )
    with pytest.raises(RuntimeError, match="configured together"):
        FileBarrierFailpoint.from_settings(partial)
    with pytest.raises(ValueError, match="must be positive"):
        FileBarrierFailpoint(tmp_path, set(), poll_interval_seconds=0)


@pytest.mark.asyncio
async def test_enabled_checkpoint_writes_reached_and_blocks_until_release(
    tmp_path: Path,
) -> None:
    settings = Settings(
        environment=Environment.TEST,
        failpoint_root=tmp_path,
        failpoint_checkpoints=Checkpoint.AFTER_INDEX_WRITE.value,
    )
    barrier = FileBarrierFailpoint.from_settings(settings)
    assert barrier is not None

    blocked = asyncio.create_task(barrier(Checkpoint.AFTER_INDEX_WRITE))
    reached = tmp_path / "after_index_write.reached"
    release = tmp_path / "after_index_write.release"
    await _wait_for_path(reached)
    assert not blocked.done()

    release.touch()
    await asyncio.wait_for(blocked, timeout=1)

    release.unlink()
    await asyncio.wait_for(barrier(Checkpoint.AFTER_INDEX_WRITE), timeout=0.1)


@pytest.mark.asyncio
async def test_disabled_checkpoint_is_noop_and_cancellation_does_not_hang(
    tmp_path: Path,
) -> None:
    barrier = FileBarrierFailpoint(tmp_path, {Checkpoint.AFTER_PARSE})
    await asyncio.wait_for(barrier(Checkpoint.AFTER_INDEX_WRITE), timeout=0.1)

    blocked = asyncio.create_task(barrier(Checkpoint.AFTER_PARSE))
    await _wait_for_path(tmp_path / "after_parse.reached")
    blocked.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(blocked, timeout=1)


async def _wait_for_path(path: Path) -> None:
    for _ in range(100):
        if path.exists():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"marker was not created: {path}")
