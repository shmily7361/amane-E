"""pending_writebacks 表与 tasks(type,status) 复合索引的迁移契约."""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from tests.helpers import alembic_config

_PREVIOUS = "dc04a8ae624e"
_TARGET = "f4c1a9e3b7d2"


def _indexes(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA index_list({table})")}


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_pending_writeback_migration_creates_table_and_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    cfg: Config = alembic_config(db_path)

    command.upgrade(cfg, _PREVIOUS)
    conn = sqlite3.connect(db_path)
    try:
        assert "pending_writebacks" not in _tables(conn)
        assert "ix_tasks_type_status" not in _indexes(conn, "tasks")
    finally:
        conn.close()

    command.upgrade(cfg, _TARGET)
    conn = sqlite3.connect(db_path)
    try:
        assert "pending_writebacks" in _tables(conn)
        assert "ix_pending_writebacks_created_at" in _indexes(conn, "pending_writebacks")
        assert "ix_tasks_type_status" in _indexes(conn, "tasks")
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='ix_tasks_type_status'"
        ).fetchone()
        assert sql is not None and "type" in sql[0] and "status" in sql[0]
    finally:
        conn.close()

    command.downgrade(cfg, _PREVIOUS)
    conn = sqlite3.connect(db_path)
    try:
        assert "pending_writebacks" not in _tables(conn)
        assert "ix_tasks_type_status" not in _indexes(conn, "tasks")
    finally:
        conn.close()


def test_pending_writeback_retry_migration(tmp_path: Path) -> None:
    """退避字段与到期索引的迁移可从 E0.29 库升级并回滚."""
    db_path = tmp_path / "retry.db"
    cfg: Config = alembic_config(db_path)
    previous = "2f7b9a4c1d3e"
    target = "5140329a6da9"

    command.upgrade(cfg, previous)
    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(pending_writebacks)")}
        assert "attempts" not in cols
    finally:
        conn.close()

    command.upgrade(cfg, target)
    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(pending_writebacks)")}
        assert {"attempts", "next_attempt_at", "last_error"} <= cols
        assert "ix_pending_writebacks_next_attempt_at" in _indexes(conn, "pending_writebacks")
        defaults = {row[1]: row[4] for row in conn.execute("PRAGMA table_info(pending_writebacks)")}
        assert str(defaults["attempts"]).strip("'") == "1"
    finally:
        conn.close()

    command.downgrade(cfg, previous)
    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(pending_writebacks)")}
        assert {"attempts", "next_attempt_at", "last_error"}.isdisjoint(cols)
        assert "ix_pending_writebacks_next_attempt_at" not in _indexes(conn, "pending_writebacks")
    finally:
        conn.close()
