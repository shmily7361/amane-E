"""到期 Schedule 创建对应 Task, 并更新 last_run / next_run."""

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from croniter import croniter
from structlog.contextvars import bound_contextvars

from ..db import RoutineType, TaskType
from ..handlers import CleanupPayload, R18ImportPayload, RescrapePayload, SemanticIndexPayload, UpscalePayload
from ..handlers.enqueue import retry_pending_writebacks

if TYPE_CHECKING:
    from ..db.repository import Repository

logger = structlog.get_logger()

_CHECK_INTERVAL = 60.0
_SHUTDOWN_TICK_TIMEOUT = 10.0


class CronScheduler:
    def __init__(self, repo: Repository):
        self._repo = repo
        self._running = False
        self._stop_signal = asyncio.Event()
        # 关停时先等当前 tick 收尾; 否则取消正在进行的 DB 写事务会留下 SQLite 写锁,
        # 紧接着的 stop_workers / 标记 RUNNING 任务失败会报 database is locked.
        self._tick_done = asyncio.Event()
        self._tick_done.set()

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True
        self._stop_signal.clear()
        logger.info("cron scheduler started", interval=_CHECK_INTERVAL)

        while self._running:
            try:
                await self._tick()
            except Exception:
                logger.exception("cron scheduler tick error")
            # 可被 stop() 立刻唤醒, 不必等满检查间隔.
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop_signal.wait(), _CHECK_INTERVAL)

    async def stop(self) -> None:
        self._running = False
        # 不在 _tick 持写锁时取消任务: 等它提交或超时后再发停止信号.
        with suppress(TimeoutError):
            await asyncio.wait_for(self._tick_done.wait(), _SHUTDOWN_TICK_TIMEOUT)
        self._stop_signal.set()
        logger.info("cron scheduler stopping")

    async def _tick(self) -> None:
        self._tick_done.clear()
        try:
            # 与用户 Schedule 正交: 入队失败登记的增量写盘每分钟自愈, 不需要用户新建定时任务.
            await retry_pending_writebacks(self._repo)
            now = datetime.now(UTC)
            schedules = await self._repo.list_schedules()

            for schedule in schedules:
                if not schedule.enabled:
                    continue
                if schedule.id is None:
                    continue

                if schedule.next_run is None:
                    # 尚无 next_run: 只写入下次触发时间
                    next_run = croniter(schedule.cron, now).get_next(datetime)
                    await self._repo.update_schedule(schedule.id, next_run=next_run)
                else:
                    # 无时区则按 UTC
                    next_run = schedule.next_run if schedule.next_run.tzinfo else schedule.next_run.replace(tzinfo=UTC)

                if next_run > now:
                    continue

                with bound_contextvars(
                    schedule_id=schedule.id, schedule_name=schedule.name, task_type=schedule.task_type
                ):
                    logger.info("schedule triggered", payload=schedule.payload)
                    await self._execute_task(schedule.id, schedule.task_type, schedule.payload)

                new_next_run = croniter(schedule.cron, now).get_next(datetime)
                await self._repo.update_schedule(schedule.id, last_run=now, next_run=new_next_run)
        finally:
            self._tick_done.set()

    async def _execute_task(self, schedule_id: int, task_type: RoutineType, payload: dict) -> None:
        match task_type:
            case RoutineType.CLEANUP:
                await self._repo.create_task(
                    TaskType.CLEANUP,
                    CleanupPayload(
                        remove_missing_files=payload.get("remove_missing_files", True),
                        remove_unreferenced_resources=payload.get("remove_unreferenced_resources", True),
                    ),
                )
            case RoutineType.UPSCALE:
                await self._repo.create_task(
                    TaskType.UPSCALE,
                    UpscalePayload(
                        max_dim_threshold=payload.get("max_dim_threshold"),
                        max_bytes_threshold=payload.get("max_bytes_threshold"),
                        limit=payload.get("limit", 200),
                    ),
                )
            case RoutineType.R18_IMPORT:
                await self._repo.create_task(TaskType.R18_IMPORT, R18ImportPayload(force=payload.get("force", False)))
            case RoutineType.RESCRAPE:
                await self._repo.create_task(TaskType.RESCRAPE, RescrapePayload.model_validate(payload))
            case RoutineType.SEMANTIC_INDEX:
                await self._repo.create_task(TaskType.SEMANTIC_INDEX, SemanticIndexPayload.model_validate(payload))
            case _:
                logger.warning("unknown task type", task_type=task_type)
