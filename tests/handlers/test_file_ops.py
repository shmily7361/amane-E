"""测试 file ops 消费已物化 URL (内部派生 / 外部) + trailer 落盘."""

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from PIL import Image

from amane.config import HotSettings, WatermarkConfig
from amane.db.models import Library, MediaFile, Metadata
from amane.enums import DownloadableResource
from amane.handlers.file import _write_into_library, execute_file_operations
from amane.media import ResourceStore
from amane.organize import MoveMode, PlaceOutcome, ResolvedPaths, SidecarMove, place_sidecars, resolve_paths
from amane.parsing import ContentType, parse_file_info

if TYPE_CHECKING:
    from amane.net.http import WebClient


class FakeClient:
    """download 写出小图/视频占位."""

    async def download(self, url: str, dest: Path, **kwargs) -> bool:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if url.endswith(".mp4"):
            dest.write_bytes(b"video")
        else:
            Image.new("RGB", (800, 538), "blue").save(dest)
        return True

    async def get_filesize(self, url: str, **kwargs):
        return None

    async def resolve_final_url(self, url: str, **kwargs) -> str:
        return url


def _pixel(path: Path) -> tuple[int, int, int]:
    with Image.open(path) as img:
        return cast("tuple[int, int, int]", img.convert("RGB").getpixel((0, 0)))


def test_library_write_skips_unchanged_attachment(tmp_path: Path) -> None:
    """全库改名整理时, 内容未变的附件不再重复复制."""
    src = tmp_path / "resources" / "trailer.mp4"
    dst = tmp_path / "lib" / "trailer.mp4"
    src.parent.mkdir(parents=True)
    dst.parent.mkdir(parents=True)
    src.write_bytes(b"same bytes")

    _write_into_library(src, dst)
    assert dst.read_bytes() == b"same bytes"
    src_stat = src.stat()
    assert dst.stat().st_mtime_ns == src_stat.st_mtime_ns

    # 目标被外部改写但大小与时间戳仍与源一致时, 短路会跳过写入 (size + mtime 判据).
    dst.write_bytes(b"sentinelxx")
    assert len(b"sentinelxx") == len(b"same bytes")
    os.utime(dst, ns=(src_stat.st_atime_ns, src_stat.st_mtime_ns))
    _write_into_library(src, dst)
    assert dst.read_bytes() == b"sentinelxx"


def test_library_write_survives_utime_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SMB/FAT 拒绝设时间戳时仍要把附件落盘, 不能让 ORGANIZE 整体失败."""
    src = tmp_path / "resources" / "poster.jpg"
    dst = tmp_path / "lib" / "poster.jpg"
    src.parent.mkdir(parents=True)
    dst.parent.mkdir(parents=True)
    src.write_bytes(b"poster-bytes")

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr("amane.handlers.file.os.utime", _boom)
    _write_into_library(src, dst)
    assert dst.read_bytes() == b"poster-bytes"


def _paths(base: Path) -> ResolvedPaths:
    return ResolvedPaths(
        video=base / "MIDV-123" / "MIDV-123.mp4",
        thumb=base / "MIDV-123" / "thumb.jpg",
        poster=base / "MIDV-123" / "poster.jpg",
        fanart=base / "MIDV-123" / "fanart.jpg",
        extrafanart_dir=base / "MIDV-123" / "extrafanart",
        nfo=base / "MIDV-123" / "MIDV-123.nfo",
        trailer=base / "MIDV-123" / "trailer.mp4",
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_rerule_organize_relocates_old_sidecars(resource_store: ResourceStore, tmp_path: Path) -> None:
    """改模板重新整理时, 旧目录的 NFO/图片/剧照/预告片/.bif 要跟着视频到新目录."""
    old = tmp_path / "old"
    old.mkdir()
    src = old / "MIDV-123.mp4"
    src.write_bytes(b"video")
    old_files = {
        old / "MIDV-123.nfo": b"old nfo",
        old / "MIDV-123-cd2.nfo": b"cd2 nfo",
        old / "poster.jpg": b"poster",
        old / "thumb.jpg": b"thumb",
        old / "fanart.jpg": b"fanart",
        old / "trailer.mp4": b"trailer",
        old / "MIDV-123.bif": b"bif",
    }
    for path, data in old_files.items():
        path.write_bytes(data)
    (old / "extrafanart").mkdir()
    (old / "extrafanart" / "1.jpg").write_bytes(b"extra")

    library = Library(
        id=1,
        name="lib",
        path=str(tmp_path),
        move_mode=MoveMode.MOVE,
        trailer_pattern="(?i)trailer",
        subtitle_extensions=[],
        patterns=[],
    )
    media_file = MediaFile(path=str(src), library_id=1)
    metadata = Metadata(number="MIDV-123", title="Movie")
    paths = _paths(tmp_path / "new")

    result = await execute_file_operations(
        media_file,
        metadata,
        paths,
        MoveMode.MOVE,
        resource_store,
        parse_file_info(str(src)),
        download_images=False,
        write_nfo=False,
        copy_resources=[],
        web_client=None,
        config=None,
        library=library,
        safe_dirs=None,
        allow_resource_download=False,
    )

    assert result.outcome is PlaceOutcome.PLACED
    assert result.dest == paths.video
    assert paths.video.read_bytes() == b"video"
    assert paths.nfo.read_bytes() == b"old nfo"
    assert paths.nfo.with_name("MIDV-123-cd2.nfo").read_bytes() == b"cd2 nfo"
    assert paths.poster.read_bytes() == b"poster"
    assert paths.thumb.read_bytes() == b"thumb"
    assert paths.fanart.read_bytes() == b"fanart"
    assert paths.trailer.read_bytes() == b"trailer"
    assert (paths.extrafanart_dir / "1.jpg").read_bytes() == b"extra"
    assert paths.video.with_suffix(".bif").read_bytes() == b"bif"
    assert not any(path.is_file() for path in old.rglob("*"))


@pytest.mark.asyncio(loop_scope="function")
async def test_sidecar_conflict_is_reported(tmp_path: Path) -> None:
    """目标已被占用时附属资源留在原处, 但冲突返回给整理结果记账."""
    old = tmp_path / "old"
    old.mkdir()
    source = old / "poster.jpg"
    source.write_bytes(b"old")
    paths = _paths(tmp_path / "new")
    paths.poster.parent.mkdir(parents=True)
    paths.poster.write_bytes(b"occupied")

    conflicts = await place_sidecars([SidecarMove(source=source, target=paths.poster)], MoveMode.MOVE)

    assert len(conflicts) == 1
    assert conflicts[0].source == source and conflicts[0].target == paths.poster
    assert source.read_bytes() == b"old"


@pytest.mark.asyncio
async def test_internal_poster_and_trailer(resource_store: ResourceStore, tmp_path: Path):
    """poster = 内部派生 URL → 从 store 取文件; trailer 下载并复制."""
    client = cast("WebClient", FakeClient())

    # 造一个裁剪派生资源, 作为 metadata 的内部 poster URL
    async def producer(dest: Path) -> bool:
        Image.new("RGB", (379, 538), "red").save(dest)
        return True

    crop = await resource_store.acquire_derived("https://s/t.jpg", "crop", "0.7", producer)
    assert crop is not None
    internal_url = f"/api/resources/{ResourceStore.url_hash(crop.url)}"

    src = tmp_path / "src" / "MIDV-123.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"movie")

    mf = MediaFile(path=str(src), library_id=1)
    meta = Metadata(
        number="MIDV-123",
        thumb_urls=["https://s/t.jpg"],
        poster_urls=[internal_url],
        trailer_urls=["https://s/trailer.mp4"],
    )
    out_base = tmp_path / "out"
    paths = _paths(out_base)

    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.COPY,
        resource_store=resource_store,
        file_info=parse_file_info(src),
        web_client=client,
        config=HotSettings(),
    )
    assert result.outcome is PlaceOutcome.PLACED
    assert paths.thumb.exists()  # 外部下载
    assert paths.poster.exists()  # 内部派生解析
    assert paths.trailer.exists()  # trailer 落盘
    assert paths.trailer.read_bytes() == b"video"


@pytest.mark.asyncio
async def test_target_occupied_returns_before_side_effects(resource_store: ResourceStore, tmp_path: Path):
    """目标被占用时立即返回: 不下载图片、不写 NFO、不动磁盘."""
    client = cast("WebClient", FakeClient())

    src = tmp_path / "src" / "MIDV-123.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"movie")

    paths = _paths(tmp_path / "out")
    paths.video.parent.mkdir(parents=True)
    paths.video.write_text("existing content")

    mf = MediaFile(path=str(src), library_id=1)
    meta = Metadata(number="MIDV-123", thumb_urls=["https://s/t.jpg"])

    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.MOVE,
        resource_store=resource_store,
        file_info=parse_file_info(src),
        web_client=client,
        config=HotSettings(),
    )

    assert result.outcome is PlaceOutcome.CONFLICT
    assert result.conflict_target == paths.video
    assert paths.video.read_text() == "existing content"
    assert src.read_bytes() == b"movie"
    assert not paths.thumb.exists()
    assert not paths.nfo.exists()


@pytest.mark.asyncio
async def test_unknown_target_state_returns_before_side_effects(
    resource_store: ResourceStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """读不出目标状态时立即返回并记为失败: 不下载图片、不写 NFO、不动磁盘."""
    client = cast("WebClient", FakeClient())

    src = tmp_path / "src" / "MIDV-123.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"movie")

    paths = _paths(tmp_path / "out")
    paths.video.parent.mkdir(parents=True)
    paths.video.write_text("existing content")

    def denied(self: Path, other: object) -> bool:
        raise PermissionError(13, "Permission denied", str(other))

    monkeypatch.setattr(Path, "samefile", denied)

    mf = MediaFile(path=str(src), library_id=1)
    meta = Metadata(number="MIDV-123", thumb_urls=["https://s/t.jpg"])

    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.MOVE,
        resource_store=resource_store,
        file_info=parse_file_info(src),
        web_client=client,
        config=HotSettings(),
    )

    assert result.outcome is PlaceOutcome.FAILED
    assert result.error is not None
    assert "Permission denied" in result.error
    assert result.conflict_target is None
    assert paths.video.read_text() == "existing content"
    assert src.read_bytes() == b"movie"
    assert not paths.thumb.exists()
    assert not paths.nfo.exists()


@pytest.mark.asyncio
async def test_copy_resources_skips_trailer(resource_store: ResourceStore, tmp_path: Path):
    """copy_resources 不含 trailer 时不落盘预告片, 仍复制封面."""
    client = cast("WebClient", FakeClient())
    src = tmp_path / "src" / "MIDV-123.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"movie")

    mf = MediaFile(path=str(src), library_id=1)
    meta = Metadata(number="MIDV-123", thumb_urls=["https://s/t.jpg"], trailer_urls=["https://s/trailer.mp4"])
    paths = _paths(tmp_path / "out")
    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.COPY,
        resource_store=resource_store,
        file_info=parse_file_info(src),
        web_client=client,
        config=HotSettings(),
        copy_resources=[DownloadableResource.thumb, DownloadableResource.poster],
    )
    assert result.outcome is PlaceOutcome.PLACED
    assert paths.thumb.exists()
    assert not paths.trailer.exists()


@pytest.mark.asyncio
async def test_write_nfo_false_skips_nfo(resource_store: ResourceStore, tmp_path: Path):
    client = cast("WebClient", FakeClient())
    src = tmp_path / "src" / "MIDV-123.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"movie")

    mf = MediaFile(path=str(src), library_id=1)
    meta = Metadata(number="MIDV-123", title="T", thumb_urls=["https://s/t.jpg"])
    paths = _paths(tmp_path / "out")
    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.COPY,
        resource_store=resource_store,
        file_info=parse_file_info(src),
        web_client=client,
        config=HotSettings(),
        write_nfo=False,
    )
    assert result.outcome is PlaceOutcome.PLACED
    assert not paths.nfo.exists()


@pytest.mark.parametrize(
    ("content_type", "expected", "desc"),
    [
        (ContentType.CENSORED, True, "有码回退裁剪"),
        (ContentType.WESTERN, False, "欧美不裁剪"),
    ],
)
@pytest.mark.asyncio
async def test_missing_internal_resource_crop_fallback_by_content_type(
    resource_store: ResourceStore, tmp_path: Path, content_type: ContentType, expected: bool, desc: str
):
    """poster 内部 URL 指向不存在资源 → 仅允许的类型回退从 thumb 裁剪."""
    client = cast("WebClient", FakeClient())
    src = tmp_path / "src" / "MIDV-123.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"movie")

    mf = MediaFile(path=str(src), library_id=1)
    meta = Metadata(
        number="MIDV-123",
        content_type=content_type,
        thumb_urls=["https://s/t.jpg"],
        poster_urls=["/api/resources/deadbeefdeadbeef"],  # 不存在
    )
    paths = _paths(tmp_path / "out")
    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.COPY,
        resource_store=resource_store,
        file_info=parse_file_info(src),
        web_client=client,
        config=HotSettings(),
    )
    assert result.outcome is PlaceOutcome.PLACED
    assert paths.poster.exists() == expected, desc


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.asyncio
async def test_watermark_respects_enabled(resource_store: ResourceStore, tmp_path: Path, enabled: bool):
    client = cast("WebClient", FakeClient())
    src = tmp_path / "src" / "MIDV-123-C.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"movie")

    user_dir = tmp_path / "watermarks"
    user_dir.mkdir()
    Image.new("RGBA", (40, 20), (255, 0, 0, 255)).save(user_dir / "subtitle.png")

    mf = MediaFile(path=str(src), library_id=1)
    meta = Metadata(number="MIDV-123", thumb_urls=["https://s/t.jpg"])
    paths = _paths(tmp_path / "out")
    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.COPY,
        resource_store=resource_store,
        file_info=parse_file_info(src, has_external_subtitle=mf.has_external_subtitle),
        web_client=client,
        config=HotSettings(watermark=WatermarkConfig(enabled=enabled, scale=0.2)),
        watermark_dir=user_dir,
    )
    assert result.outcome is PlaceOutcome.PLACED
    with Image.open(paths.thumb) as img:
        pixel = img.convert("RGB").getpixel((20, 20))
    assert isinstance(pixel, tuple)
    if enabled:
        assert pixel[0] > 180 and pixel[2] < 80
    else:
        assert pixel[2] > 180 and pixel[0] < 80


@pytest.mark.asyncio
async def test_watermark_from_external_subtitle(resource_store: ResourceStore, tmp_path: Path):
    """文件名没有 -C 时, 库里的同目录字幕命中同样打出封面中字角标."""
    client = cast("WebClient", FakeClient())
    src = tmp_path / "src" / "MIDV-123.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"movie")

    user_dir = tmp_path / "watermarks"
    user_dir.mkdir()
    Image.new("RGBA", (40, 20), (255, 0, 0, 255)).save(user_dir / "subtitle.png")

    mf = MediaFile(path=str(src), library_id=1, has_external_subtitle=True)
    meta = Metadata(number="MIDV-123", thumb_urls=["https://s/t.jpg"])
    paths = _paths(tmp_path / "out")
    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.COPY,
        resource_store=resource_store,
        file_info=parse_file_info(src, has_external_subtitle=mf.has_external_subtitle),
        web_client=client,
        config=HotSettings(watermark=WatermarkConfig(enabled=True, scale=0.2)),
        watermark_dir=user_dir,
    )
    assert result.outcome is PlaceOutcome.PLACED
    with Image.open(paths.thumb) as img:
        pixel = img.convert("RGB").getpixel((20, 20))
    assert isinstance(pixel, tuple)
    assert pixel[0] > 180 and pixel[2] < 80


@pytest.mark.asyncio
async def test_in_place_rewrites_existing_artifacts(resource_store: ResourceStore, tmp_path: Path) -> None:
    """源已在目标路径上时仍重写库路径上的旧产物: NFO 按本次元数据, 图片与预告片按本次资源.

    fanart 与 thumb 同源, 因此与 thumb 一同重写.
    """
    client = cast("WebClient", FakeClient())

    paths = _paths(tmp_path / "out")
    paths.video.parent.mkdir(parents=True)
    paths.video.write_bytes(b"movie")
    paths.nfo.write_text("旧 NFO", encoding="utf-8")
    stale = (paths.thumb, paths.poster, paths.fanart)
    before = {}
    for image in stale:
        Image.new("RGB", (800, 538), "red").save(image)
        before[image] = _pixel(image)
    paths.trailer.write_bytes(b"old trailer")

    mf = MediaFile(path=str(paths.video), library_id=1)
    meta = Metadata(
        number="MIDV-123",
        title="新标题",
        thumb_urls=["https://s/t.jpg"],
        poster_urls=["https://s/p.jpg"],
        trailer_urls=["https://s/trailer.mp4"],
    )

    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.MOVE,
        resource_store=resource_store,
        file_info=parse_file_info(paths.video),
        web_client=client,
        config=HotSettings(),
    )

    assert result.outcome is PlaceOutcome.PLACED
    assert result.dest == paths.video
    assert "新标题" in paths.nfo.read_text(encoding="utf-8")
    for image in stale:
        assert _pixel(image) != before[image]
    assert paths.trailer.read_bytes() == b"video"


@pytest.mark.parametrize("pre_existing", [True, False])
@pytest.mark.asyncio
async def test_fanart_follows_thumb_selection(
    resource_store: ResourceStore, tmp_path: Path, pre_existing: bool
) -> None:
    """thumb 不在 copy_resources 时 fanart 既不重写也不补建."""
    client = cast("WebClient", FakeClient())

    paths = _paths(tmp_path / "out")
    paths.video.parent.mkdir(parents=True)
    paths.video.write_bytes(b"movie")
    if pre_existing:
        Image.new("RGB", (800, 538), "red").save(paths.fanart)
    before = _pixel(paths.fanart) if pre_existing else None

    mf = MediaFile(path=str(paths.video), library_id=1)
    meta = Metadata(number="MIDV-123", thumb_urls=["https://s/t.jpg"], poster_urls=["https://s/p.jpg"])

    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.MOVE,
        resource_store=resource_store,
        file_info=parse_file_info(paths.video),
        web_client=client,
        config=HotSettings(),
        copy_resources=[DownloadableResource.poster],
    )

    assert result.outcome is PlaceOutcome.PLACED
    assert not paths.thumb.exists()
    assert paths.poster.exists()
    assert paths.fanart.exists() is pre_existing
    if before is not None:
        assert _pixel(paths.fanart) == before


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 无 POSIX 权限位")
def test_write_into_library_takes_permissions_from_target(tmp_path: Path) -> None:
    """模式位来自目标目录, 不继承源文件."""
    source = tmp_path / "src.jpg"
    source.write_bytes(b"image")
    source.chmod(0o600)
    probe = tmp_path / "probe"
    probe.write_bytes(b"")
    target = tmp_path / "dst.jpg"

    _write_into_library(source, target)

    assert target.read_bytes() == b"image"
    assert target.stat().st_mode & 0o777 == probe.stat().st_mode & 0o777


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 无 POSIX 权限位")
def test_write_into_library_rewrites_existing_artifact(tmp_path: Path) -> None:
    """目标已存在时按新文件重写, 旧产物的模式位不保留."""
    source = tmp_path / "src.jpg"
    source.write_bytes(b"new")
    probe = tmp_path / "probe"
    probe.write_bytes(b"")
    target = tmp_path / "dst.jpg"
    target.write_bytes(b"old")
    target.chmod(0o600)

    _write_into_library(source, target)

    assert target.read_bytes() == b"new"
    assert target.stat().st_mode & 0o777 == probe.stat().st_mode & 0o777


def test_write_into_library_keeps_target_on_write_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """写入失败时保留旧产物, 不留下临时文件, 并带上路径记一条错误日志."""
    source = tmp_path / "src.jpg"
    target = tmp_path / "dst.jpg"
    target.write_bytes(b"old")
    recorded: list[tuple[str, dict[str, object]]] = []

    def reject(*args: object, **kwargs: object) -> Path:
        raise OSError(22, "Invalid argument")

    def record(event: str, **fields: object) -> None:
        recorded.append((event, fields))

    monkeypatch.setattr("amane.handlers.file.shutil.copyfile", reject)
    monkeypatch.setattr("amane.handlers.file.logger.error", record)

    with pytest.raises(OSError):
        _write_into_library(source, target)

    assert target.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["dst.jpg"]
    assert [(event, fields["source"], fields["target"]) for event, fields in recorded] == [
        ("library write failed", str(source), str(target))
    ]


@pytest.mark.asyncio
async def test_extrafanart_names_follow_library_template(resource_store: ResourceStore, tmp_path: Path):
    """剧照文件名按库模板逐张渲染; 宽度由本轮数量决定."""
    client = cast("WebClient", FakeClient())
    root = tmp_path / "lib"
    root.mkdir()
    src = tmp_path / "src" / "MIDV-123.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"movie")

    mf = MediaFile(path=str(src), library_id=1)
    meta = Metadata(
        number="MIDV-123",
        extrafanart_urls={"dmm": [f"https://s/e{i}.jpg" for i in range(1, 11)]},
    )
    lib = Library(
        name="t",
        path=str(root),
        video_template="{number}/{number}.{ext}",
        extrafanart_name_template="{number}-{index_padded}.jpg",
    )
    paths = resolve_paths(lib, meta, ext="mp4", source_path=src)

    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.COPY,
        resource_store=resource_store,
        file_info=parse_file_info(src),
        web_client=client,
        config=HotSettings(),
        library=lib,
    )

    assert result.outcome is PlaceOutcome.PLACED
    names = sorted(p.name for p in paths.extrafanart_dir.iterdir())
    assert len(names) == 10
    assert names[0] == "MIDV-123-01.jpg"
    assert names[-1] == "MIDV-123-10.jpg"


@pytest.mark.asyncio
async def test_existing_stills_in_dir_are_left_alone(resource_store: ResourceStore, tmp_path: Path):
    """换模板或张数跨位数后旧名字留在目录里: 本 PR 只写本次的写集, 不清理目录."""
    client = cast("WebClient", FakeClient())
    root = tmp_path / "lib"
    root.mkdir()
    src = tmp_path / "src" / "MIDV-123.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"movie")

    mf = MediaFile(path=str(src), library_id=1)
    meta = Metadata(number="MIDV-123", extrafanart_urls={"dmm": ["https://s/e1.jpg", "https://s/e2.jpg"]})
    lib = Library(name="t", path=str(root), video_template="{number}/{number}.{ext}")
    paths = resolve_paths(lib, meta, ext="mp4", source_path=src)
    paths.extrafanart_dir.mkdir(parents=True)
    stale = paths.extrafanart_dir / "99.jpg"
    stale.write_bytes(b"old")

    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.COPY,
        resource_store=resource_store,
        file_info=parse_file_info(src),
        web_client=client,
        config=HotSettings(),
        library=lib,
    )

    assert result.outcome is PlaceOutcome.PLACED
    assert stale.exists()
    assert sorted(p.name for p in paths.extrafanart_dir.iterdir()) == ["1.jpg", "2.jpg", "99.jpg"]


@pytest.mark.asyncio
async def test_extrafanart_names_fall_back_without_library(resource_store: ResourceStore, tmp_path: Path):
    """未带库列时用默认模板, 与传入路径时的默认输出一致."""
    client = cast("WebClient", FakeClient())
    root = tmp_path / "lib"
    root.mkdir()
    src = tmp_path / "src" / "MIDV-123.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"movie")

    mf = MediaFile(path=str(src), library_id=1)
    meta = Metadata(number="MIDV-123", extrafanart_urls={"dmm": ["https://s/e1.jpg", "https://s/e2.jpg"]})
    lib = Library(name="t", path=str(root), video_template="{number}/{number}.{ext}")
    paths = resolve_paths(lib, meta, ext="mp4", source_path=src)

    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.COPY,
        resource_store=resource_store,
        file_info=parse_file_info(src),
        web_client=client,
        config=HotSettings(),
    )

    assert result.outcome is PlaceOutcome.PLACED
    assert sorted(p.name for p in paths.extrafanart_dir.iterdir()) == ["1.jpg", "2.jpg"]


class NoNetworkClient:
    async def download(self, url: str, dest: Path, **kwargs) -> bool:
        raise AssertionError(f"整理阶段不应联网下载: {url}")

    async def get_filesize(self, url: str, **kwargs):
        raise AssertionError(f"整理阶段不应探测远端: {url}")

    async def resolve_final_url(self, url: str, **kwargs) -> str:
        raise AssertionError(f"整理阶段不应解析远端: {url}")


@pytest.mark.asyncio
async def test_organize_never_downloads_missing_resources(resource_store: ResourceStore, tmp_path: Path):
    """缓存未命中时跳过附属文件, 整段落盘不发起网络请求."""
    src = tmp_path / "src" / "MIDV-123.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"movie")
    mf = MediaFile(path=str(src), library_id=1)
    meta = Metadata(
        number="MIDV-123",
        thumb_urls=["https://s/thumb.jpg"],
        trailer_urls=["https://s/trailer.mp4"],
        extrafanart_urls={"dmm": ["https://s/e1.jpg"]},
    )
    paths = _paths(tmp_path / "out")

    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.COPY,
        resource_store=resource_store,
        file_info=parse_file_info(src),
        web_client=cast("WebClient", NoNetworkClient()),
        config=HotSettings(),
        write_nfo=False,
        allow_resource_download=False,
    )

    assert result.outcome is PlaceOutcome.PLACED
    assert paths.video.exists()
    assert not paths.thumb.exists()
    assert not paths.trailer.exists()
    assert not paths.extrafanart_dir.exists()
