"""Smoke tests for the top-level CLI skeleton."""

import sys
from pathlib import Path

from click.testing import CliRunner

import tg_cli
from tg_cli import cli

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def _project_version() -> str:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    return tomllib.loads(pyproject.read_text())["project"]["version"]


def test_entry_point_exists() -> None:
    """`tg_cli.cli.main` must be importable and a Click group."""
    assert callable(cli.main)
    assert hasattr(cli.main, "commands")


def test_help_runs_cleanly() -> None:
    """`tg --help` exits 0 and mentions the project description."""
    runner = CliRunner()
    result = runner.invoke(cli.main, ["--help"])
    assert result.exit_code == 0
    assert "Telegram" in result.output


def test_version_prints_the_project_version() -> None:
    """`tg --version` exits 0 even without installed package metadata,
    and prints the version `pyproject.toml` declares."""
    runner = CliRunner()
    result = runner.invoke(cli.main, ["--version"])
    assert result.exit_code == 0
    assert result.output.endswith(f", version {_project_version()}\n")


def test_package_carries_the_project_version() -> None:
    assert tg_cli.__version__ == _project_version()
