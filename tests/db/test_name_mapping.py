"""文件名 → 番号映射的仓储契约."""

from typing import TYPE_CHECKING

import pytest

from amane.utils.path import path_key

if TYPE_CHECKING:
    from amane.db.repository import Repository


@pytest.mark.asyncio(loop_scope="function")
async def test_name_mapping_upsert_get_and_list(repo: Repository) -> None:
    old = "/inbox/UNKNOWN-CLIP.mp4"
    new = "/library/archive/UNKNOWN-CLIP.mp4"
    assert await repo.get_name_mapping(old) is None

    await repo.upsert_name_mapping(old, " ABC-123 ")
    assert await repo.get_name_mapping(new) == "ABC-123"
    assert await repo.list_name_mappings() == {path_key("UNKNOWN-CLIP.mp4"): "ABC-123"}

    await repo.upsert_name_mapping(new, "NEW-456")
    assert await repo.get_name_mapping(old) == "NEW-456"
    assert await repo.list_name_mappings() == {path_key("UNKNOWN-CLIP.mp4"): "NEW-456"}


@pytest.mark.asyncio(loop_scope="function")
async def test_name_mapping_ignores_empty_input(repo: Repository) -> None:
    await repo.upsert_name_mapping("", "IGNORED")
    await repo.upsert_name_mapping("/inbox/empty.mp4", "   ")
    assert await repo.list_name_mappings() == {}
