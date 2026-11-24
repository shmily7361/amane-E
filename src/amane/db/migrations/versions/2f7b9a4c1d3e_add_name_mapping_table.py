"""add name mapping table

Revision ID: 2f7b9a4c1d3e
Revises: f4c1a9e3b7d2
Create Date: 2026-11-24 11:30:48.932899

手动指定番号且刮削成功后, 把「文件名比较键 → 番号」持久化; 下次同名文件直接套用.
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2f7b9a4c1d3e"
down_revision: str | None = "f4c1a9e3b7d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "name_mappings",
        sa.Column("file_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("number", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("file_name"),
    )
    op.create_index(op.f("ix_name_mappings_number"), "name_mappings", ["number"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_name_mappings_number"), table_name="name_mappings")
    op.drop_table("name_mappings")
