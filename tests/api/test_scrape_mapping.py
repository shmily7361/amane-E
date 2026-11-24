"""提交仅带 media_id 的刮削时, 文件名映射优先于正则解析."""

from typing import TYPE_CHECKING

import pytest

from amane.api.models.tasks import ScrapeRequest

if TYPE_CHECKING:
    from amane.db.repository import Repository


@pytest.mark.asyncio(loop_scope="function")
async def test_scrape_request_uses_name_mapping(repo: Repository) -> None:
    library = await repo.create_library(name="lib", path="/inbox")
    assert library.id is not None
    media = await repo.create_media_file(library_id=library.id, path="/inbox/weird-name.mp4")
    assert media.id is not None
    await repo.upsert_name_mapping(media.path, "MAPPED-001")

    payload = await ScrapeRequest(media_id=media.id).resolve(repo)

    assert payload.number == "MAPPED-001"
    assert payload.media_file_id == media.id
    # 映射不是用户本次显式输入, 不应重复登记.
    assert payload.number_explicit is False


@pytest.mark.asyncio(loop_scope="function")
async def test_scrape_request_explicit_number_wins(repo: Repository) -> None:
    library = await repo.create_library(name="lib", path="/inbox")
    assert library.id is not None
    media = await repo.create_media_file(library_id=library.id, path="/inbox/weird-name.mp4")
    assert media.id is not None
    await repo.upsert_name_mapping(media.path, "MAPPED-001")

    payload = await ScrapeRequest(media_id=media.id, number="TYPED-002").resolve(repo)

    assert payload.number == "TYPED-002"
    assert payload.number_explicit is True
