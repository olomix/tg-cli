"""Tests for ``tg_cli.client.make_client``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tg_cli.client import make_client
from tg_cli.config import Config


def test_make_client_creates_dir_and_passes_session_path(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "cfg"
    cfg = Config(api_id=42, api_hash="hash", config_dir=config_dir)
    with patch("tg_cli.client.TelegramClient") as mock_tc:
        client = make_client(cfg)
    assert config_dir.is_dir()
    mock_tc.assert_called_once_with(str(config_dir / "session"), 42, "hash")
    assert client is mock_tc.return_value


def test_make_client_loads_config_when_none(tmp_path: Path) -> None:
    cfg = Config(api_id=7, api_hash="h", config_dir=tmp_path)
    with (
        patch("tg_cli.client.load_config", return_value=cfg) as mock_load,
        patch("tg_cli.client.TelegramClient") as mock_tc,
    ):
        make_client()
    mock_load.assert_called_once_with()
    mock_tc.assert_called_once_with(str(tmp_path / "session"), 7, "h")
