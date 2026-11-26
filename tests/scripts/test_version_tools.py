"""fork 版本工具的契约测试."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import bump_fork_version as fork_mod
from scripts import export_openapi as export_mod
from scripts.bump_version import BumpError
from scripts.changelog import extract_section
from scripts.check_version_consistency import collect, pyproject_version

ROOT = Path(__file__).resolve().parents[2]
OLD = "0.29.0+e0.29"
NEW = "0.30.0+e0.30"
PATCH = "0.29.0+e0.29.1"


def _run(script: str, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), *args],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _init_repo(
    tmp_path: Path,
    *,
    heading: str = "E0.30",
    upstream_tag: str = "v0.30.0",
    upstream_heading: str = "v0.30.0",
) -> Path:
    root = tmp_path / "fork"
    (root / "web" / "src" / "client").mkdir(parents=True)
    (root / "pyproject.toml").write_text(f'[project]\nname = "amane"\nversion = "{OLD}"\n', encoding="utf-8")
    (root / "uv.lock").write_text(
        f'version = 1\n\n[[package]]\nname = "amane"\nversion = "{OLD}"\nsource = {{ editable = "." }}\n',
        encoding="utf-8",
    )
    (root / "web" / "openapi.json").write_text(json.dumps({"info": {"version": OLD}, "paths": {}}), encoding="utf-8")
    (root / "web" / "src" / "client" / "index.ts").write_text("export {};\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text(
        f"## {heading}\n\n- fork note\n\n## {upstream_heading}\n\n- upstream note\n",
        encoding="utf-8",
    )
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "init")
    _git(root, "tag", upstream_tag)
    return root


def test_changelog_prefers_exact_heading(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("## E0.30\n\n- fork note\n\n## v0.30.0\n\n- upstream note\n", encoding="utf-8")

    fork = _run("changelog.py", "extract", "E0.30", "--file", str(changelog))
    pep440 = _run("changelog.py", "extract", NEW, "--file", str(changelog))
    official = _run("changelog.py", "extract", "0.30.0", "--file", str(changelog))

    assert fork.returncode == pep440.returncode == official.returncode == 0
    assert "fork note" in fork.stdout and "fork note" in pep440.stdout
    assert "upstream note" in official.stdout and "fork note" not in official.stdout


def test_parse_fork_target_supports_optional_patch() -> None:
    target = fork_mod.parse_target("E0.29.1")

    assert (target.display, target.base, target.full, target.tag) == (
        "E0.29.1",
        "0.29.0",
        PATCH,
        "vE0.29.1",
    )
    assert fork_mod.parse_target("E0.29").full == OLD
    for invalid in ("E0.29.1.2", "0.29.1"):
        with pytest.raises(BumpError):
            fork_mod.parse_target(invalid)


def test_changelog_supports_three_segment_alias() -> None:
    text = "## E0.29.1\n\n- fork note\n\n## v0.29.0\n\n- upstream note\n"

    assert "fork note" in extract_section(text, "E0.29.1")
    assert "fork note" in extract_section(text, PATCH)
    assert "upstream note" in extract_section(text, "0.29.0")


def test_export_openapi_uses_pyproject_version(tmp_path: Path, monkeypatch) -> None:
    class FakeApp:
        version = "stale"

        def openapi(self) -> dict:
            return {"info": {"version": self.version}}

    app = FakeApp()
    monkeypatch.setattr(export_mod, "create_app", lambda: app)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "web").mkdir()
    (root / "pyproject.toml").write_text(f'[project]\nname = "amane"\nversion = "{NEW}"\n', encoding="utf-8")

    assert export_mod.main(root=root) == 0

    written = json.loads((root / "web" / "openapi.json").read_text(encoding="utf-8"))
    assert written["info"]["version"] == NEW


def test_version_consistency_cli() -> None:
    result = _run("check_version_consistency.py")

    assert result.returncode == 0, result.stderr
    assert "pyproject:" in result.stdout
    assert "runtime:" in result.stdout


def test_bump_fork_dry_run_reports_target(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)

    assert fork_mod.main(["E0.30", "--dry-run"], root=root) == 0
    assert pyproject_version(root) == OLD
    assert collect(root, include_runtime=False)["openapi"] == OLD


def test_bump_fork_write_generate_verify_roundtrip(tmp_path: Path, monkeypatch) -> None:
    root = _init_repo(tmp_path)

    def _generate(target_root: Path) -> None:
        version = pyproject_version(target_root)
        (target_root / "web" / "openapi.json").write_text(
            json.dumps({"info": {"version": version}, "paths": {}}), encoding="utf-8"
        )

    monkeypatch.setattr(fork_mod, "generate", _generate)

    assert fork_mod.main(["E0.30"], root=root) == 0
    assert set(collect(root, include_runtime=False).values()) == {NEW}
    assert "release: E0.30" in _git_log_subject(root)
    assert (
        "vE0.30"
        in subprocess.run(["git", "tag", "--list"], cwd=root, capture_output=True, text=True).stdout.splitlines()
    )


def test_bump_fork_patch_roundtrip(tmp_path: Path, monkeypatch) -> None:
    root = _init_repo(
        tmp_path,
        heading="E0.29.1",
        upstream_tag="v0.29.0",
        upstream_heading="v0.29.0",
    )

    def _generate(target_root: Path) -> None:
        version = pyproject_version(target_root)
        (target_root / "web" / "openapi.json").write_text(
            json.dumps({"info": {"version": version}, "paths": {}}), encoding="utf-8"
        )

    monkeypatch.setattr(fork_mod, "generate", _generate)

    assert fork_mod.main(["E0.29.1"], root=root) == 0
    assert set(collect(root, include_runtime=False).values()) == {PATCH}
    assert "release: E0.29.1" in _git_log_subject(root)
    assert (
        "vE0.29.1"
        in subprocess.run(["git", "tag", "--list"], cwd=root, capture_output=True, text=True).stdout.splitlines()
    )


def test_bump_fork_rolls_back_after_failed_verification(tmp_path: Path, monkeypatch) -> None:
    root = _init_repo(tmp_path)

    def _bad_generate(_target_root: Path) -> None:
        raise RuntimeError("codegen failed")

    monkeypatch.setattr(fork_mod, "generate", _bad_generate)

    assert fork_mod.main(["E0.30"], root=root) == 1
    assert set(collect(root, include_runtime=False).values()) == {OLD}
    assert (
        "vE0.30"
        not in subprocess.run(["git", "tag", "--list"], cwd=root, capture_output=True, text=True).stdout.splitlines()
    )


def _git_log_subject(root: Path) -> str:
    return subprocess.run(["git", "log", "-1", "--pretty=%s"], cwd=root, capture_output=True, text=True).stdout.strip()
