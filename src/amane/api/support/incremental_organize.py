"""元数据编辑后的增量落盘入队.

ORGANIZE 已支持 ``media_file_ids`` 精确范围; 这里只负责把编辑过的条目翻译成对应文件,
按库分组入队, 且失败不影响编辑请求本身.
"""

from collections import defaultdict
from collections.abc import Sequence

import structlog

from ...db.models import TaskType
from ...db.repository import Repository
from ...handlers.models import OrganizePayload

logger = structlog.get_logger()


async def enqueue_incremental_organize(repo: Repository, metadata_ids: Sequence[int]) -> list[int]:
    """为编辑过的条目入队只写对应文件的 ORGANIZE, 返回任务 id; 无关联文件时返回空列表.

    落盘范围不含视频搬移以外的目录清理, 因此 ``prune_empty_dirs=False``.
    """
    ids = list(dict.fromkeys(metadata_id for metadata_id in metadata_ids if metadata_id is not None))
    if not ids:
        return []
    try:
        files = await repo.list_media_files(metadata_ids=ids, limit=None)
        groups: dict[int, list[int]] = defaultdict(list)
        for media_file in files:
            if media_file.id is not None:
                groups[media_file.library_id].append(media_file.id)
        task_ids: list[int] = []
        for library_id, media_file_ids in groups.items():
            payload = OrganizePayload(
                library_id=library_id,
                media_file_ids=media_file_ids,
                prune_empty_dirs=False,
            )
            await payload.resolve(repo)
            task = await repo.create_task(TaskType.ORGANIZE, payload)
            if task.id is not None:
                task_ids.append(task.id)
        return task_ids
    except Exception:
        # 落盘是编辑的后续动作, 入队失败不得让已经成功的编辑请求变成 500.
        logger.warning("incremental organize enqueue failed", metadata_ids=ids, exc_info=True)
        return []
