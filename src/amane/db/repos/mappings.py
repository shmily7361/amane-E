"""文件名 → 番号映射的持久化.

只在用户显式指定番号且刮削成功后写入; 同名文件再次出现时按文件名比较键命中, 不需要重解析.
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import col, select

from ...utils.path import path_key
from ..models import NameMapping
from ..repo_types import _utcnow
from .base import RepositoryMixinBase, acquire_write_lock


def _key(path: str | Path) -> str:
    name = Path(path).name
    return path_key(name) if name else ""


class NameMappingRepoMixin(RepositoryMixinBase):
    async def upsert_name_mapping(self, path: str | Path, number: str) -> None:
        """登记文件名到番号; 已存在时更新番号, 保留首次登记时间."""
        key = _key(path)
        normalized = number.strip()
        if not key or not normalized:
            return
        now = _utcnow()
        async with self._session() as session:
            await acquire_write_lock(session)
            row = await session.get(NameMapping, key)
            if row is None:
                session.add(NameMapping(file_name=key, number=normalized, created_at=now, updated_at=now))
            elif row.number != normalized:
                row.number = normalized
                row.updated_at = now
                session.add(row)
            await session.commit()

    async def get_name_mapping(self, path: str | Path) -> str | None:
        """按文件名比较键取已确认的番号; 没有映射返回 None."""
        key = _key(path)
        if not key:
            return None
        async with self._session() as session:
            row = (await session.exec(select(NameMapping).where(col(NameMapping.file_name) == key))).one_or_none()
            return row.number if row is not None else None

    async def list_name_mappings(self) -> dict[str, str]:
        """一次取全表供扫描/刷新复用; 映射表规模随用户手动修正数增长, 通常很小."""
        async with self._session() as session:
            rows = (await session.exec(select(NameMapping))).all()
            return {row.file_name: row.number for row in rows}
