"""兼容导出: 增量落盘入队实现已迁到 ``handlers.enqueue``.

API 层保留此模块一个版本, 避免一次性改动所有调用点; agent / scheduler 直接使用领域层实现.
"""

from ...handlers.enqueue import (
    IncrementalOrganizeError,
    enqueue_incremental_organize,
    retry_pending_writebacks,
)

__all__ = ["IncrementalOrganizeError", "enqueue_incremental_organize", "retry_pending_writebacks"]
