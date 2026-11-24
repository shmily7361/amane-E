"""元数据编辑后的增量落盘入队 (领域层, 不依赖 HTTP 适配层).

ORGANIZE 已支持 ``media_file_ids`` 精确范围; 这里负责把编辑过的条目翻译成对应文件、按库分组
入队, 并在失败时登记待补偿条目. 编辑请求本身不因落盘入队失败而回滚.

失败告警走结构化日志: ``WSEventLogHandler`` 会把 ``logger.warning`` 的 event_dict 转成
``EventType.LOG`` 推到前端 /logs 页, 因此这里不需要给每个调用点注入 EventBus.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import TYPE_CHECKING

import structlog

from ..db.models import TaskType
from ..db.repo_types import PENDING_WRITEBACK_MAX_ATTEMPTS
from .models import OrganizePayload, organize_dedupe_key

if TYPE_CHECKING:
    from ..db.repository import Repository

logger = structlog.get_logger()

_RETRY_BATCH_SIZE = 100
_RETRY_LOG_ATTEMPTS = frozenset({2, 5, 10})
"""重试失败时的汇总 INFO 节点; 其余重试只写 DEBUG, 避免 /logs 刷屏."""


class IncrementalOrganizeError(RuntimeError):
    """内部入队校验失败; 不泄漏 FastAPI HTTPException 到 agent / scheduler."""


async def enqueue_incremental_organize(
    repo: Repository, metadata_ids: Sequence[int], *, retry_failure: bool = False
) -> list[int]:
    """为编辑过的条目入队只写对应文件的 ORGANIZE, 返回任务 id.

    成功入队 (或无关联文件无需落盘) 的条目清除待补偿标记; 失败的条目写入 ``pending_writebacks``.
    `retry_failure` 为真表示这是 cron 重投, 失败次数累加并走退避/死信日志分级.
    """
    ids = list(dict.fromkeys(metadata_id for metadata_id in metadata_ids if metadata_id is not None))
    if not ids:
        return []

    try:
        files = await repo.list_media_files(metadata_ids=ids, limit=None)
    except Exception as exc:
        await _register_failure(repo, ids, exc, retry=retry_failure)
        return []

    groups: dict[int, list[int]] = defaultdict(list)
    group_metadata_ids: dict[int, set[int]] = defaultdict(set)
    for media_file in files:
        if media_file.id is None:
            continue
        groups[media_file.library_id].append(media_file.id)
        if media_file.metadata_id is not None:
            group_metadata_ids[media_file.library_id].add(media_file.metadata_id)

    task_ids: list[int] = []
    succeeded: set[int] = set()
    failed: set[int] = set()
    failures: list[str] = []
    grouped_metadata_ids: set[int] = set()

    for library_id, media_file_ids in groups.items():
        owned = sorted(group_metadata_ids[library_id] & set(ids))
        grouped_metadata_ids.update(owned)
        try:
            library = await repo.get_library(library_id)
            if library is None:
                raise IncrementalOrganizeError(f"媒体库 {library_id} 不存在")
            payload = OrganizePayload(
                library_id=library_id,
                media_file_ids=media_file_ids,
                prune_empty_dirs=False,
                dedupe_key=organize_dedupe_key(library_id, media_file_ids),
            )
            # 内部路径只补库默认值; 不调用会抛 HTTPException 的 payload.resolve().
            payload.apply_library_defaults(library)
            task = await repo.create_task(TaskType.ORGANIZE, payload)
            if task.id is not None:
                task_ids.append(task.id)
            succeeded.update(owned)
        except Exception as exc:
            failed.update(owned)
            failures.append(f"library {library_id}: {exc}")

    # 无关联文件的条目没有落盘需求, 也视为已处理; 避免它们永远留在待补偿表里.
    succeeded.update(set(ids) - grouped_metadata_ids)
    if succeeded:
        try:
            await repo.clear_metadata_pending_writeback(sorted(succeeded))
        except Exception:
            logger.warning("clear pending writeback failed", metadata_ids=sorted(succeeded), exc_info=True)

    if failed:
        await _register_failure(
            repo,
            sorted(failed),
            RuntimeError("; ".join(failures) or "incremental organize enqueue failed"),
            retry=retry_failure,
        )
    return task_ids


async def retry_pending_writebacks(repo: Repository, *, limit: int = _RETRY_BATCH_SIZE) -> list[int]:
    """cron 调用: 重投此前入队失败的增量写盘, 成功后由 enqueue 清除标记."""
    try:
        ids = await repo.list_pending_writeback_metadata_ids(limit=limit)
    except Exception:
        logger.warning("list pending writeback failed", exc_info=True)
        return []
    if not ids:
        return []
    return await enqueue_incremental_organize(repo, ids, retry_failure=True)


async def _register_failure(
    repo: Repository, metadata_ids: Sequence[int], exc: Exception, *, retry: bool = False
) -> None:
    ids = sorted(set(metadata_ids))
    error = str(exc)
    try:
        attempts = await repo.mark_metadata_pending_writeback(ids, error=error, retry=retry)
    except Exception:
        # DB 已不可写时无法登记补偿; 至少 WARNING 日志已经发出.
        logger.warning("mark pending writeback failed", metadata_ids=ids, exc_info=True)
        return

    if not retry:
        # 结构化字段会被 WSEventLogHandler 原样带进 EventType.LOG.data, 前端 /logs 可直接看到影响范围.
        logger.warning(
            "incremental organize enqueue failed; pending writeback registered",
            metadata_ids=ids,
            error=error,
            exc_info=True,
        )
        return

    dead = [metadata_id for metadata_id in ids if attempts.get(metadata_id, 1) >= PENDING_WRITEBACK_MAX_ATTEMPTS]
    if dead:
        logger.error(
            "pending writeback dead letter",
            metadata_ids=dead,
            attempts=PENDING_WRITEBACK_MAX_ATTEMPTS,
            error=error,
        )
    notable = [
        metadata_id
        for metadata_id in ids
        if metadata_id not in dead and attempts.get(metadata_id, 1) in _RETRY_LOG_ATTEMPTS
    ]
    if notable:
        logger.info(
            "pending writeback retry scheduled",
            metadata_ids=notable,
            attempts={str(metadata_id): attempts[metadata_id] for metadata_id in notable},
            error=error,
        )
    quiet = [metadata_id for metadata_id in ids if metadata_id not in dead and metadata_id not in notable]
    if quiet:
        logger.debug("pending writeback retry failed", metadata_ids=quiet, error=error)
