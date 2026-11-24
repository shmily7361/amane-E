#!/usr/bin/env python3
"""从 FastAPI 应用导出 OpenAPI schema, 供前端 SDK 生成 (无需启动服务器).

用法: uv run python scripts/export_openapi.py
输出: web/openapi.json

版本号以源码树的 ``pyproject.toml`` 为准, 不读运行环境的 dist-info; 这样发版脚本写入新版本后,
同一进程内重新导出就能得到新版本, CI 也不依赖是否刚做过 ``uv sync``.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from amane.api.app import create_app


def project_version(root: Path) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def main(root: Path | None = None) -> int:
    root = root or Path(__file__).resolve().parents[1]
    out = root / "web" / "openapi.json"
    app = create_app()
    app.version = project_version(root)
    # newline="\n" 固定 LF: Windows 上 Path.write_text 默认会做 \n → \r\n 转写,
    # 导致 regen 后的 openapi.json 与仓库内 LF 版产生整文件 diff, 误报 check-openapi.
    out.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
