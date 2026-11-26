"""allow metadata trailer only

Revision ID: 240c841b3619
Revises: 5140329a6da9
Create Date: 2026-11-26 12:33:19.901359

新增库级开关: 是否允许只有元数据/预告片的目录独立存在. 默认关闭.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "240c841b3619"
down_revision: str | None = "5140329a6da9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "libraries",
        sa.Column(
            "allow_metadata_trailer_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("libraries") as batch:
        batch.drop_column("allow_metadata_trailer_only")
