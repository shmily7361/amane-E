"""pending writeback retry and task exclusive index

Revision ID: f4c1a9e3b7d2
Revises: dc04a8ae624e
Create Date: 2026-11-23 18:26:53.931418

- 增量 ORGANIZE 入队失败时登记待重试条目, 避免编辑成功却永久丢失落盘.
- tasks(type, status) 复合索引让互斥键查询先按类型/状态收窄, 不再逐行扫全部活跃任务.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4c1a9e3b7d2"
down_revision: str | None = "dc04a8ae624e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pending_writebacks",
        sa.Column("metadata_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["metadata_id"], ["metadata.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("metadata_id"),
    )
    op.create_index("ix_pending_writebacks_created_at", "pending_writebacks", ["created_at"], unique=False)
    # _active_exclusive_ids 固定带 type + status 过滤; 单列 index 只能选其一, 复合索引让每次入队
    # 只扫描同类型、同状态的活跃行, 而不是全部 QUEUED/RUNNING 任务.
    op.create_index("ix_tasks_type_status", "tasks", ["type", "status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tasks_type_status", table_name="tasks")
    op.drop_index("ix_pending_writebacks_created_at", table_name="pending_writebacks")
    op.drop_table("pending_writebacks")
