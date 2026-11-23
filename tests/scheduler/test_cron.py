"""定时任务: 到期的 SEMANTIC_INDEX 例行提交对应任务, 载荷原样转换."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from amane.db import RoutineType, TaskType
from amane.handlers import SemanticIndexPayload
from amane.scheduler.cron import CronScheduler

if TYPE_CHECKING:
    from pathlib import Path

    from amane.db.repository import Repository


@pytest.mark.asyncio
async def test_due_semantic_schedule_enqueues_index_task(repo: Repository) -> None:
    schedule = await repo.create_schedule(
        cron="0 3 * * *",
        task_type=RoutineType.SEMANTIC_INDEX,
        payload={"type": "semantic_index", "limit": 50, "force": True},
        next_run=datetime.now(UTC) - timedelta(minutes=1),
    )
    assert schedule.id is not None

    await CronScheduler(repo)._tick()

    tasks = await repo.list_tasks(task_types=[TaskType.SEMANTIC_INDEX])
    assert len(tasks) == 1
    payload = SemanticIndexPayload.model_validate(tasks[0].payload)
    assert (payload.limit, payload.force) == (50, True)
    updated = await repo.get_schedule(schedule.id)
    assert updated is not None and updated.last_run is not None


@pytest.mark.asyncio
async def test_future_semantic_schedule_stays_idle(repo: Repository) -> None:
    await repo.create_schedule(
        cron="0 3 * * *",
        task_type=RoutineType.SEMANTIC_INDEX,
        payload={"type": "semantic_index"},
        next_run=datetime.now(UTC) + timedelta(hours=1),
    )

    await CronScheduler(repo)._tick()

    assert await repo.list_tasks(task_types=[TaskType.SEMANTIC_INDEX]) == []


@pytest.mark.asyncio
async def test_tick_retries_pending_writeback(repo: Repository, tmp_path: Path) -> None:
    """待补偿条目不依赖用户新建 Schedule; 每次 cron tick 自动重投并清除."""
    lib = await repo.create_library(name="wb", path=str(tmp_path / "lib"))
    assert lib.id is not None
    meta = await repo.upsert_metadata(number="CRON-WB")
    assert meta.id is not None
    media = await repo.create_media_file(lib.id, path=str(tmp_path / "lib" / "A.mp4"), metadata_id=meta.id)
    assert media.id is not None
    await repo.mark_metadata_pending_writeback([meta.id])

    await CronScheduler(repo)._tick()

    tasks = await repo.list_tasks(task_types=[TaskType.ORGANIZE])
    assert len(tasks) == 1
    assert tasks[0].payload["media_file_ids"] == [media.id]
    assert await repo.list_pending_writeback_metadata_ids() == []


@pytest.mark.asyncio
async def test_stop_waits_for_in_flight_tick(repo: Repository) -> None:
    """关停不能取消正在写库的 tick; 否则随后标记 RUNNING 任务失败会撞上 SQLite 写锁."""
    scheduler = CronScheduler(repo)
    scheduler._tick_done.clear()
    stopping = asyncio.create_task(scheduler.stop())
    await asyncio.sleep(0)
    assert not stopping.done()

    scheduler._tick_done.set()
    await asyncio.wait_for(stopping, 1)
    assert stopping.done()
