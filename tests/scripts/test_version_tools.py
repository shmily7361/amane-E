"""fork 版本工具的契约测试."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(script: str, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), *args],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
    )


def test_changelog_accepts_fork_heading(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("## E0.29\n\n- fork note\n\n## v0.29.0\n\n- upstream note\n", encoding="utf-8")

    by_display = _run("changelog.py", "extract", "E0.29", "--file", str(changelog))
    by_pep440 = _run("changelog.py", "extract", "0.29.0+e0.29", "--file", str(changelog))

    assert by_display.returncode == 0, by_display.stderr
    assert by_pep440.returncode == 0, by_pep440.stderr
    assert "fork note" in by_display.stdout
    assert "fork note" in by_pep440.stdout
    assert "upstream note" not in by_pep440.stdout


def test_version_consistency_cli() -> None:
    result = _run("check_version_consistency.py")

    assert result.returncode == 0, result.stderr
    assert "pyproject:" in result.stdout
    assert "runtime:" in result.stdout


def test_bump_fork_dry_run_reports_target() -> None:
    result = _run("bump_fork_version.py", "E0.29", "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "target:  0.29.0+e0.29" in result.stdout
    assert "tag:     vE0.29" in result.stdout
    assert "dry-run: 未写入" in result.stdout
