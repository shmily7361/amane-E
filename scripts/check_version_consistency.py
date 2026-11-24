#!/usr/bin/env python3
"""检查产品版本在 pyproject / uv.lock / OpenAPI / 运行时四处一致.

``just check`` 会调用本脚本; 发版脚本也会在写入后复用这里的读取函数.
"""

from __future__ import annotations

import json
import sys
import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class VersionMismatch(Exception):
    """用户可见的版本不一致."""


def pyproject_version(root: Path = ROOT) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def lock_version(root: Path = ROOT) -> str:
    data = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    for package in data["package"]:
        if package.get("name") == "amane":
            return str(package["version"])
    raise VersionMismatch("uv.lock 缺少 amane 包")


def openapi_version(root: Path = ROOT) -> str:
    data = json.loads((root / "web/openapi.json").read_text(encoding="utf-8"))
    return str(data["info"]["version"])


def runtime_version() -> str:
    try:
        return installed_version("amane")
    except PackageNotFoundError as e:
        raise VersionMismatch("运行时未安装 amane; 先执行 uv sync") from e


def collect(root: Path = ROOT, *, include_runtime: bool = True) -> dict[str, str]:
    versions = {
        "pyproject": pyproject_version(root),
        "uv.lock": lock_version(root),
        "openapi": openapi_version(root),
    }
    if include_runtime:
        versions["runtime"] = runtime_version()
    return versions


def check(root: Path = ROOT) -> dict[str, str]:
    versions = collect(root)
    for name, value in versions.items():
        print(f"{name}: {value}")  # noqa: T201
    if len(set(versions.values())) != 1:
        raise VersionMismatch("版本不一致: pyproject / uv.lock / openapi / runtime 必须相同")
    return versions


def main() -> int:
    try:
        check()
    except VersionMismatch as e:
        print(f"版本一致性检查失败: {e}", file=sys.stderr)  # noqa: T201
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
