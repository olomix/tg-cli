"""Tests for ``tg_cli.config``."""

from __future__ import annotations

from pathlib import Path

import pytest

from tg_cli import config as cfg_mod
from tg_cli.config import Config, ConfigError, load_config


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_load_config_success(tmp_path: Path) -> None:
    _write(
        tmp_path / "config.toml",
        'api_id = 12345\napi_hash = "abcdef0123"\n',
    )
    c = load_config(tmp_path)
    assert isinstance(c, Config)
    assert c.api_id == 12345
    assert c.api_hash == "abcdef0123"
    assert c.config_dir == tmp_path
    assert c.session_path == tmp_path / "session"


def test_load_config_missing_file_has_setup_hint(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path)
    msg = str(exc.value)
    assert str(tmp_path / "config.toml") in msg
    assert "my.telegram.org" in msg
    assert "tg login" in msg


def test_load_config_malformed_toml(tmp_path: Path) -> None:
    _write(tmp_path / "config.toml", "api_id = not a number\n")
    with pytest.raises(ConfigError, match="Malformed TOML"):
        load_config(tmp_path)


def test_load_config_missing_api_id(tmp_path: Path) -> None:
    _write(tmp_path / "config.toml", 'api_hash = "x"\n')
    with pytest.raises(ConfigError, match="Missing 'api_id'"):
        load_config(tmp_path)


def test_load_config_missing_api_hash(tmp_path: Path) -> None:
    _write(tmp_path / "config.toml", "api_id = 1\n")
    with pytest.raises(ConfigError, match="Missing 'api_hash'"):
        load_config(tmp_path)


def test_load_config_api_id_wrong_type(tmp_path: Path) -> None:
    _write(tmp_path / "config.toml", 'api_id = "1"\napi_hash = "x"\n')
    with pytest.raises(ConfigError, match="api_id.*must be an int"):
        load_config(tmp_path)


def test_load_config_api_id_bool_rejected(tmp_path: Path) -> None:
    """`bool` is a subclass of `int`; must still be rejected."""
    _write(tmp_path / "config.toml", 'api_id = true\napi_hash = "x"\n')
    with pytest.raises(ConfigError, match="api_id.*must be an int"):
        load_config(tmp_path)


def test_load_config_api_hash_wrong_type(tmp_path: Path) -> None:
    _write(tmp_path / "config.toml", "api_id = 1\napi_hash = 42\n")
    with pytest.raises(ConfigError, match="api_hash.*must be a str"):
        load_config(tmp_path)


def test_env_var_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        tmp_path / "config.toml",
        'api_id = 999\napi_hash = "envhash"\n',
    )
    monkeypatch.setenv(cfg_mod.ENV_CONFIG_DIR, str(tmp_path))
    c = load_config()
    assert c.api_id == 999
    assert c.api_hash == "envhash"
    assert c.config_dir == tmp_path


def test_get_config_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cfg_mod.ENV_CONFIG_DIR, raising=False)
    assert cfg_mod.get_config_dir() == Path.home() / ".config" / "tg-cli"


def test_get_config_dir_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cfg_mod.ENV_CONFIG_DIR, str(tmp_path))
    assert cfg_mod.get_config_dir() == tmp_path


def test_get_config_dir_env_expands_tilde(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(cfg_mod.ENV_CONFIG_DIR, "~/custom-tg")
    assert cfg_mod.get_config_dir() == Path.home() / "custom-tg"


def test_load_config_tightens_file_perms(tmp_path: Path) -> None:
    """``api_hash`` is a long-lived secret; load must chmod 0o600."""
    path = tmp_path / "config.toml"
    _write(path, 'api_id = 1\napi_hash = "secret"\n')
    path.chmod(0o644)
    load_config(tmp_path)
    assert path.stat().st_mode & 0o777 == 0o600
