#!/usr/bin/env python3
"""自定义分支的 E 版本发版: 检查 → 写版本 → 生成契约 → 白名单提交/tag.

用法::

    python scripts/bump_fork_version.py E0.30
    python scripts/bump_fork_version.py E0.30 --dry-run

E0.30 对应 PEP 440 版本 ``0.30.0+e0.30``: base 必须已合入 ``v0.30.0``, 展示 tag 为 ``vE0.30``.
只本地提交与打 tag, 不 push.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.bump_version import (  # noqa: E402
    ALLOWED_PATHS,
    PREBUMP_ALLOWED_PATHS,
    BumpError,
    allowed_changed,
    extra_paths,
    git,
    git_changed_paths,
    tag_exists,
)
from scripts.check_version_consistency import collect, pyproject_version  # noqa: E402

_TARGET = re.compile(r"^E(?P<major>\d+)\.(?P<minor>\d+)$")


@dataclass(frozen=True, slots=True)
class ForkVersion:
    display: str
    base: str
    full: str

    @property
    def tag(self) -> str:
        return f"v{self.display}"


def parse_target(raw: str) -> ForkVersion:
    match = _TARGET.match(raw.strip())
    if match is None:
        raise BumpError(f"目标格式应为 E<major>.<minor>, 收到: {raw}")
    major, minor = match.group("major"), match.group("minor")
    return ForkVersion(
        display=f"E{major}.{minor}",
        base=f"{major}.{minor}.0",
        full=f"{major}.{minor}.0+e{major}.{minor}",
    )


def _run(cmd: list[str], cwd: Path) -> None:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
        raise BumpError(f"{' '.join(cmd)} 失败: {detail}")


def require_ancestor(root: Path, base: str) -> None:
    tag = f"v{base}"
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", tag, "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise BumpError(f"官方 {tag} 不在 HEAD 祖先中, 先合入对应上游版本")


def require_changelog(root: Path, display: str) -> None:
    script = Path(__file__).resolve().parent / "changelog.py"
    _run([sys.executable, str(script), "extract", display, "--file", str(root / "CHANGELOG.md")], root)


def write_versions(root: Path, version: str) -> None:
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    updated, count = re.subn(r'(?m)^version = "[^"]+"$', f'version = "{version}"', text, count=1)
    if count != 1:
        raise BumpError("pyproject.toml 未找到唯一的 version 行")
    pyproject.write_text(updated, encoding="utf-8", newline="\n")

    lock = root / "uv.lock"
    text = lock.read_text(encoding="utf-8")
    pattern = r'(?ms)(\[\[package\]\]\nname = "amane"\nversion = ")[^"]+(")'
    updated, count = re.subn(pattern, lambda m: f"{m.group(1)}{version}{m.group(2)}", text, count=1)
    if count != 1:
        raise BumpError("uv.lock 未找到唯一的 amane 包版本")
    lock.write_text(updated, encoding="utf-8", newline="\n")


def generate(root: Path) -> None:
    _run([sys.executable, str(ROOT / "scripts" / "export_openapi.py")], root)
    if shutil.which("pnpm") is None:
        print("pnpm 不可用: 跳过 TS client 生成, 请手动执行 pnpm gen-client")  # noqa: T201
        return
    _run(["pnpm", "gen-client"], root / "web")


def verify_written(root: Path) -> None:
    versions = collect(root, include_runtime=False)
    for name, value in versions.items():
        print(f"{name}: {value}")  # noqa: T201
    if len(set(versions.values())) != 1:
        raise BumpError("写入后版本不一致")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", help="E<major>.<minor>, 例如 E0.30")
    parser.add_argument("--dry-run", action="store_true", help="只检查并打印目标, 不写文件")
    args = parser.parse_args(argv)

    try:
        target = parse_target(args.target)
        current = pyproject_version(ROOT)
        unexpected = extra_paths(git_changed_paths(ROOT), PREBUMP_ALLOWED_PATHS)
        if unexpected:
            raise BumpError(f"工作区不干净, 拒绝 bump: {', '.join(sorted(unexpected))}")
        require_ancestor(ROOT, target.base)
        require_changelog(ROOT, target.display)
        if tag_exists(ROOT, target.tag):
            raise BumpError(f"tag 已存在: {target.tag}")
        if target.full == current and not args.dry_run:
            raise BumpError(f"目标版本与当前版本相同: {current}")

        print(f"current: {current}")  # noqa: T201
        print(f"target:  {target.full}")  # noqa: T201
        print(f"tag:     {target.tag}")  # noqa: T201
        if args.dry_run:
            print("dry-run: 未写入")  # noqa: T201
            return 0

        write_versions(ROOT, target.full)
        generate(ROOT)
        verify_written(ROOT)

        changed = git_changed_paths(ROOT)
        unexpected = extra_paths(changed, ALLOWED_PATHS)
        if unexpected:
            raise BumpError(f"bump 改动了白名单以外的文件, 已中止提交: {', '.join(sorted(unexpected))}")
        to_add = allowed_changed(changed, ALLOWED_PATHS)
        if "pyproject.toml" not in to_add:
            raise BumpError("未改动 pyproject.toml")
        git(ROOT, "add", "--", *to_add)
        git(ROOT, "commit", "-m", f"release: {target.display}")
        git(ROOT, "tag", "-a", target.tag, "-m", target.tag)
    except BumpError as e:
        print(f"发版失败: {e}", file=sys.stderr)  # noqa: T201
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
