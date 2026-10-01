"""Smoke tests for the top-level CLI skeleton."""

from pathlib import Path

from click.testing import CliRunner

import tg_cli
from tg_cli import cli


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


def test_version_runs_cleanly() -> None:
    """`tg --version` exits 0 even without installed package metadata."""
    runner = CliRunner()
    result = runner.invoke(cli.main, ["--version"])
    assert result.exit_code == 0
    assert "0.2.0" in result.output


def test_package_and_project_metadata_carry_the_same_version() -> None:
    """The version is written in three places that must not drift."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert tg_cli.__version__ == "0.2.0"
    assert 'version = "0.2.0"' in pyproject.read_text().splitlines()
