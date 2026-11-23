"""增量写盘入队: 失败可见 + 持久待重试 + 成功后清除."""

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from amane.db.models import TaskType
from amane.handlers import enqueue as enqueue_module
from amane.handlers.enqueue import enqueue_incremental_organize, retry_pending_writebacks

if TYPE_CHECKING:
    from amane.db.repository import Repository


class _WarningRecorder:
    """直接替换模块 logger: 避免全量套件里 structlog 全局配置影响断言."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def warning(self, event: str, **kwargs: object) -> None:
        self.calls.append((event, kwargs))


async def _seed(repo: Repository, tmp_path: Path) -> tuple[int, int]:
    lib = await repo.create_library(name="wb", path=str(tmp_path / "lib"))
    assert lib.id is not None
    meta = await repo.upsert_metadata(number="WB-ENQUEUE")
    assert meta.id is not None
    media = await repo.create_media_file(lib.id, path=str(tmp_path / "lib" / "A.mp4"), metadata_id=meta.id)
    assert media.id is not None
    return meta.id, media.id


@pytest.mark.asyncio(loop_scope="function")
async def test_failure_marks_pending_and_retry_recovers(
    repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    metadata_id, media_id = await _seed(repo, tmp_path)
    warnings = _WarningRecorder()

    async def _boom(*_args: object, **_kwargs: object) -> list[object]:
        raise RuntimeError("db down")

    with monkeypatch.context() as patch:
        patch.setattr(enqueue_module, "logger", warnings)
        patch.setattr(repo, "list_media_files", _boom)
        assert await enqueue_incremental_organize(repo, [metadata_id]) == []

    assert any(
        event == "incremental organize enqueue failed; pending writeback registered"
        and kwargs.get("metadata_ids") == [metadata_id]
        and kwargs.get("error") == "db down"
        for event, kwargs in warnings.calls
    )
    assert await repo.list_pending_writeback_metadata_ids() == [metadata_id]

    assert len(await retry_pending_writebacks(repo)) == 1
    assert await repo.list_pending_writeback_metadata_ids() == []
    tasks = await repo.list_tasks(task_types=[TaskType.ORGANIZE])
    assert len(tasks) == 1
    assert tasks[0].payload["media_file_ids"] == [media_id]
    assert tasks[0].payload["prune_empty_dirs"] is False
    assert tasks[0].payload["write_nfo"] is not None
    assert tasks[0].payload["copy_resources"] is not None


@pytest.mark.asyncio(loop_scope="function")
async def test_success_clears_existing_pending_marker(repo: Repository, tmp_path: Path) -> None:
    metadata_id, _media_id = await _seed(repo, tmp_path)
    await repo.mark_metadata_pending_writeback([metadata_id])
    assert await repo.list_pending_writeback_metadata_ids() == [metadata_id]

    assert len(await enqueue_incremental_organize(repo, [metadata_id])) == 1
    assert await repo.list_pending_writeback_metadata_ids() == []


@pytest.mark.asyncio(loop_scope="function")
async def test_metadata_without_files_clears_pending_marker(repo: Repository) -> None:
    """没有关联文件的条目没有落盘需求, 不应永远留在待补偿表里."""
    meta = await repo.upsert_metadata(number="WB-EMPTY")
    assert meta.id is not None
    await repo.mark_metadata_pending_writeback([meta.id])

    assert await enqueue_incremental_organize(repo, [meta.id]) == []
    assert await repo.list_pending_writeback_metadata_ids() == []
