"""Tests for ``scripts/install-skill.sh``.

The script symlinks the repo's ``skill/`` directory into
``$HOME/.claude/skills/telegram``. We exercise it against a throwaway
``HOME`` to avoid touching the real one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "install-skill.sh"
SOURCE = REPO_ROOT / "skill"


def _run(home: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_script_exists_and_is_executable() -> None:
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK)


def test_creates_symlink(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    link = tmp_path / ".claude" / "skills" / "telegram"
    assert link.is_symlink()
    assert Path(os.readlink(link)) == SOURCE
    assert (link / "SKILL.md").is_file()


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    first = _run(tmp_path)
    assert first.returncode == 0
    second = _run(tmp_path)
    assert second.returncode == 0
    assert "already installed" in second.stdout


def test_errors_when_target_is_regular_file(tmp_path: Path) -> None:
    target_parent = tmp_path / ".claude" / "skills"
    target_parent.mkdir(parents=True)
    target = target_parent / "telegram"
    target.write_text("not a symlink\n")

    result = _run(tmp_path)

    assert result.returncode != 0
    assert "not a symlink" in result.stderr
    assert target.is_file() and not target.is_symlink()


def test_errors_when_target_is_directory(tmp_path: Path) -> None:
    target = tmp_path / ".claude" / "skills" / "telegram"
    target.mkdir(parents=True)
    (target / "stray.md").write_text("preexisting\n")

    result = _run(tmp_path)

    assert result.returncode != 0
    assert "not a symlink" in result.stderr
    assert target.is_dir() and not target.is_symlink()


def test_errors_when_symlink_points_elsewhere(tmp_path: Path) -> None:
    other = tmp_path / "other-skill"
    other.mkdir()
    target_parent = tmp_path / ".claude" / "skills"
    target_parent.mkdir(parents=True)
    target = target_parent / "telegram"
    target.symlink_to(other)

    result = _run(tmp_path)

    assert result.returncode != 0
    assert "refusing to replace" in result.stderr
    assert Path(os.readlink(target)) == other


@pytest.mark.skipif(
    shutil.which("shellcheck") is None, reason="shellcheck not installed"
)
def test_shellcheck_clean() -> None:
    result = subprocess.run(
        ["shellcheck", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
