"""重新整理时把旧目录里的已知附属资源带到新视频旁边.

修改路径模板后, 视频会被搬到新目录, 但上一轮写下的 NFO / 海报 / 剧照 / 预告片 / Emby .bif
仍留在旧目录; ``prune_empty_dirs`` 只删空目录, 清理清单又会把预告片当作媒体, 因此这类残留
很难靠事后清理回收. 这里在视频就位、重新生成本次资源之前, 按「可解释的名字」把旧资源搬过去.
只处理明确属于该视频或单视频目录内的固定产物名, 不递归吞掉用户自定义文件.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..enums import MoveMode
from ..library.rules import (
    DEFAULT_SUBTITLE_EXTENSIONS,
    DEFAULT_TRAILER_PATTERN,
    MEDIA_EXTENSIONS,
    compile_skip_patterns,
)
from ..utils.path import is_name_surrogate_dir, path_key
from ..utils.threads import in_thread
from .file import PlaceOutcome, execute_organize
from .path_templates import ResolvedPaths

if TYPE_CHECKING:
    from re import Pattern

    from ..db.models import Library

logger = structlog.get_logger()

_IMAGE_SUFFIXES = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".avif",
        ".gif",
        ".bmp",
        ".tif",
        ".tiff",
        ".heic",
        ".jfif",
        ".tbn",
    }
)
_EXTRAFANART_DIRNAMES = frozenset({"extrafanart", "extrafanarts", "fanart"})


@dataclass(frozen=True, slots=True)
class SidecarMove:
    source: Path
    target: Path


@dataclass(frozen=True, slots=True)
class SidecarConflict:
    source: Path
    target: Path


@dataclass(frozen=True, slots=True)
class _SidecarContext:
    source_stem: str
    target_video: Path
    paths: ResolvedPaths
    trailer_matcher: Pattern[str] | None
    subtitle_extensions: frozenset[str]
    single_video_dir: bool

    def is_trailer(self, path: Path) -> bool:
        return self.trailer_matcher is not None and bool(self.trailer_matcher.search(path.name))


@in_thread
def discover_sidecars(
    source: Path,
    target_video: Path,
    paths: ResolvedPaths,
    *,
    library: Library | None = None,
) -> list[SidecarMove]:
    """发现旧视频目录里应随主视频搬走的附属资源.

    ``source`` 是搬移前的视频路径; 视频本身可能已经不在磁盘上, 但父目录仍保留旧产物.
    """
    source_dir = source.parent
    if not source_dir.is_dir():
        return []

    trailer_pattern = library.trailer_pattern if library is not None else DEFAULT_TRAILER_PATTERN
    compiled = compile_skip_patterns([trailer_pattern])
    trailer_matcher = compiled[0] if compiled else None
    subtitle_extensions = frozenset(
        ext.lower() for ext in (library.subtitle_extensions if library is not None else DEFAULT_SUBTITLE_EXTENSIONS)
    )
    ctx = _SidecarContext(
        source_stem=source.stem,
        target_video=target_video,
        paths=paths,
        trailer_matcher=trailer_matcher,
        subtitle_extensions=subtitle_extensions,
        single_video_dir=_single_video_dir(source, library=library, trailer_matcher=trailer_matcher),
    )

    try:
        children = sorted(source_dir.iterdir(), key=lambda p: p.name.casefold())
    except OSError:
        return []

    moves: list[SidecarMove] = []
    seen_targets: set[str] = set()

    def add(candidate: Path, target: Path) -> None:
        key = path_key(target)
        if key in seen_targets or path_key(candidate) == key:
            return
        seen_targets.add(key)
        moves.append(SidecarMove(source=candidate, target=target))

    for child in children:
        if child.is_dir() and not child.is_symlink() and not is_name_surrogate_dir(child):
            if child.name.casefold() in _EXTRAFANART_DIRNAMES and ctx.single_video_dir:
                _add_extrafanart(child, paths.extrafanart_dir, add=add)
            continue
        if not child.is_file() or child == source:
            continue
        if child.suffix.lower() in subtitle_extensions:
            # 字幕有自己的模板与冲突记账, 不在这里重复搬.
            continue

        owned = _belongs_to_video(child, ctx.source_stem)
        if not owned and not ctx.single_video_dir:
            continue

        target = _target_for(child, ctx)
        if target is not None:
            add(child, target)
    return moves


@in_thread
def place_sidecars(moves: Sequence[SidecarMove], mode: MoveMode) -> list[SidecarConflict]:
    """按主视频同样的落盘方式搬运附属资源; 冲突和失败只记日志, 不阻断整理."""
    conflicts: list[SidecarConflict] = []
    for move in moves:
        result = execute_organize.sync(
            source=move.source,
            target_dir=move.target.parent,
            target_stem=move.target.stem,
            mode=mode,
            suffix=move.target.suffix,
        )
        if result.outcome is PlaceOutcome.CONFLICT:
            logger.warning("sidecar target occupied", source=str(move.source), target=str(move.target))
            conflicts.append(SidecarConflict(source=move.source, target=move.target))
        elif result.outcome is PlaceOutcome.FAILED:
            logger.warning(
                "sidecar organize failed", source=str(move.source), target=str(move.target), error=result.error
            )
    return conflicts


def _single_video_dir(source: Path, *, library: Library | None, trailer_matcher: Pattern[str] | None) -> bool:
    """源目录是否只含这一部正片; 多部影片共用目录时不搬固定名产物, 避免张冠李戴."""
    try:
        children = list(source.parent.iterdir())
    except OSError:
        return False
    for child in children:
        if child == source or not child.is_file():
            continue
        if trailer_matcher is not None and trailer_matcher.search(child.name):
            continue
        if library is not None and library.patterns:
            is_media = any(child.match(pattern) for pattern in library.patterns)
        else:
            is_media = child.suffix.lower() in MEDIA_EXTENSIONS
        if is_media:
            return False
    return True


def _belongs_to_video(path: Path, source_stem: str) -> bool:
    stem = path.stem.casefold()
    video = source_stem.casefold()
    return stem == video or stem.startswith((f"{video}.", f"{video}-"))


def _target_for(child: Path, ctx: _SidecarContext) -> Path | None:
    name = child.name.casefold()
    suffix = child.suffix.lower()
    paths = ctx.paths

    if ctx.is_trailer(child):
        return paths.trailer
    if suffix == ".bif":
        remainder = child.stem[len(ctx.source_stem) :] if _belongs_to_video(child, ctx.source_stem) else ""
        return ctx.target_video.parent / f"{ctx.target_video.stem}{remainder}.bif"
    if suffix == ".nfo":
        return paths.nfo
    if suffix in _IMAGE_SUFFIXES:
        if "poster" in name:
            return paths.poster
        if "fanart" in name:
            return paths.fanart
        if "thumb" in name or "cover" in name:
            return paths.thumb
        # 与视频同名的老封面或编号剧照: 保留文件名放进 extrafanart, 不猜它对应哪一类产物.
        return paths.extrafanart_dir / child.name
    return None


def _add_extrafanart(directory: Path, target_dir: Path, *, add: Callable[[Path, Path], None]) -> None:
    """把旧剧照目录内的普通文件按相对路径搬到新剧照目录."""
    try:
        files = [p for p in directory.rglob("*") if p.is_file() and not p.is_symlink()]
    except OSError:
        return
    for file in sorted(files, key=lambda p: p.as_posix().casefold()):
        add(file, target_dir / file.relative_to(directory))
