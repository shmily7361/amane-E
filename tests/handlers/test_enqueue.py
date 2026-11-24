"""增量写盘入队: 失败可见 + 持久待重试 + 成功后清除."""

from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from amane.db.models import TaskType
from amane.db.repo_types import PENDING_WRITEBACK_MAX_ATTEMPTS
from amane.db.repos import metadata as metadata_repo_module
from amane.handlers import enqueue as enqueue_module
from amane.handlers.enqueue import enqueue_incremental_organize, retry_pending_writebacks

if TYPE_CHECKING:
    from amane.db.repository import Repository


class _WarningRecorder:
    """直接替换模块 logger: 避免全量套件里 structlog 全局配置影响断言."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.infos: list[tuple[str, dict[str, object]]] = []
        self.debugs: list[tuple[str, dict[str, object]]] = []
        self.errors: list[tuple[str, dict[str, object]]] = []

    def warning(self, event: str, **kwargs: object) -> None:
        self.calls.append((event, kwargs))

    def info(self, event: str, **kwargs: object) -> None:
        self.infos.append((event, kwargs))

    def debug(self, event: str, **kwargs: object) -> None:
        self.debugs.append((event, kwargs))

    def error(self, event: str, **kwargs: object) -> None:
        self.errors.append((event, kwargs))


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


@pytest.mark.asyncio(loop_scope="function")
async def test_retry_backoff_and_dead_letter(repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """重试按次数退避, 未到期不进批次; 达到上限后落死信并停止自动重试."""
    metadata_id, _media_id = await _seed(repo, tmp_path)
    recorder = _WarningRecorder()
    real_now = metadata_repo_module._utcnow
    clock = {"now": real_now()}

    async def _boom(*_args: object, **_kwargs: object) -> list[object]:
        raise RuntimeError("still down")

    with monkeypatch.context() as patch:
        patch.setattr(enqueue_module, "logger", recorder)
        patch.setattr(metadata_repo_module, "_utcnow", lambda: clock["now"])
        patch.setattr(repo, "list_media_files", _boom)

        await enqueue_incremental_organize(repo, [metadata_id])
        row = await repo.get_pending_writeback(metadata_id)
        assert row is not None and row.attempts == 1
        assert row.next_attempt_at is not None and row.next_attempt_at <= clock["now"]

        await retry_pending_writebacks(repo)
        row = await repo.get_pending_writeback(metadata_id)
        assert row is not None and row.attempts == 2
        assert row.next_attempt_at is not None and row.next_attempt_at > clock["now"]
        first_delay = row.next_attempt_at
        assert await repo.list_pending_writeback_metadata_ids() == []

        for _ in range(PENDING_WRITEBACK_MAX_ATTEMPTS - 2):
            clock["now"] = first_delay + timedelta(seconds=1)
            await retry_pending_writebacks(repo)
            row = await repo.get_pending_writeback(metadata_id)
            assert row is not None
            if row.next_attempt_at is not None:
                first_delay = row.next_attempt_at

    assert row is not None and row.attempts == PENDING_WRITEBACK_MAX_ATTEMPTS
    assert row.next_attempt_at is None
    assert await repo.list_pending_writeback_metadata_ids() == []
    warnings = [
        call
        for call in recorder.calls
        if call[0] == "incremental organize enqueue failed; pending writeback registered"
    ]
    assert len(warnings) == 1
    assert any(event == "pending writeback dead letter" for event, _kwargs in recorder.errors)
