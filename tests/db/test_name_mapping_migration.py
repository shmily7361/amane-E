"""文件名→番号映射表的迁移契约."""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from tests.helpers import alembic_config

_PREVIOUS = "f4c1a9e3b7d2"
_TARGET = "2f7b9a4c1d3e"


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_name_mapping_migration(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    cfg: Config = alembic_config(db_path)

    command.upgrade(cfg, _PREVIOUS)
    conn = sqlite3.connect(db_path)
    try:
        assert "name_mappings" not in _tables(conn)
    finally:
        conn.close()

    command.upgrade(cfg, _TARGET)
    conn = sqlite3.connect(db_path)
    try:
        assert "name_mappings" in _tables(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(name_mappings)")}
        assert cols == {"file_name", "number", "created_at", "updated_at"}
        indexes = {str(row[1]) for row in conn.execute("PRAGMA index_list(name_mappings)")}
        assert "ix_name_mappings_number" in indexes
    finally:
        conn.close()

    command.downgrade(cfg, _PREVIOUS)
    conn = sqlite3.connect(db_path)
    try:
        assert "name_mappings" not in _tables(conn)
    finally:
        conn.close()
