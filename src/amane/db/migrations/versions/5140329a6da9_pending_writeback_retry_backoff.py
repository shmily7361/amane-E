"""pending writeback retry backoff

Revision ID: 5140329a6da9
Revises: 2f7b9a4c1d3e
Create Date: 2026-11-24 14:25:44.899740

补偿重试加退避与死信上限: 新增 attempts / next_attempt_at / last_error.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5140329a6da9"
down_revision: str | None = "2f7b9a4c1d3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pending_writebacks",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("pending_writebacks", sa.Column("next_attempt_at", sa.DateTime(), nullable=True))
    op.add_column("pending_writebacks", sa.Column("last_error", sa.String(), nullable=True))
    op.execute("UPDATE pending_writebacks SET next_attempt_at = created_at WHERE next_attempt_at IS NULL")
    op.create_index("ix_pending_writebacks_next_attempt_at", "pending_writebacks", ["next_attempt_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_pending_writebacks_next_attempt_at", table_name="pending_writebacks")
    with op.batch_alter_table("pending_writebacks") as batch:
        batch.drop_column("last_error")
        batch.drop_column("next_attempt_at")
        batch.drop_column("attempts")
