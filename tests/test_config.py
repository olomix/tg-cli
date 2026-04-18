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


def test_load_config_unreadable_file_surfaces_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A realistic permission error on ``config.toml`` must surface as a
    ``ConfigError`` with the "Could not read" message — distinct from the
    "Missing config" setup hint — so the user knows to fix perms rather
    than re-run setup. Mocked open() portably simulates chmod 000 across
    OSes (real chmod is moot when the test runs as root, e.g. in CI)."""
    path = tmp_path / "config.toml"
    _write(path, 'api_id = 1\napi_hash = "x"\n')

    real_open = Path.open

    def _raising_open(self: Path, *args: object, **kwargs: object) -> object:
        if self == path:
            raise PermissionError(13, "Permission denied", str(self))
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", _raising_open)
    with pytest.raises(ConfigError, match="Could not read"):
        load_config(tmp_path)


def test_load_config_permission_error_not_reported_as_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for the ``path.exists()`` bypass: when the file
    (or an ancestor directory) is unreadable, ``exists()`` historically
    returns ``False`` and the old guard mistakenly surfaced the
    "not configured" setup hint. The open-then-handle flow must route
    ``PermissionError`` to "Could not read" and must NOT mention the
    setup URL/"tg login" cues that belong on the true-missing path."""
    path = tmp_path / "config.toml"
    _write(path, 'api_id = 1\napi_hash = "x"\n')

    real_open = Path.open

    def _raising_open(self: Path, *args: object, **kwargs: object) -> object:
        if self == path:
            raise PermissionError(13, "Permission denied", str(self))
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", _raising_open)
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path)
    msg = str(exc.value)
    assert "Could not read" in msg
    assert "my.telegram.org" not in msg
    assert "tg login" not in msg


def test_load_config_read_error_surfaces_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``tomllib.load`` can raise ``OSError`` mid-read (EIO on flaky FS,
    NFS stall, etc.) even when ``open()`` succeeded. That failure must
    round-trip through ``ConfigError`` so the CLI's JSON-error contract
    holds instead of leaking a raw traceback."""
    path = tmp_path / "config.toml"
    _write(path, 'api_id = 1\napi_hash = "x"\n')

    def _raising_load(_f: object) -> object:
        raise OSError("flaky fs")

    monkeypatch.setattr(cfg_mod.tomllib, "load", _raising_load)
    with pytest.raises(ConfigError, match="Could not read") as exc:
        load_config(tmp_path)
    assert str(path) in str(exc.value)


def test_load_config_disappeared_file_surfaces_missing_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the file disappears between ``exists()`` and ``open()`` (race
    with a deploy/rotation) — or was never there — the setup hint is the
    right response: ``FileNotFoundError`` from ``open()`` is
    indistinguishable from a truly absent file, so both route through
    the same "not configured" message."""
    path = tmp_path / "config.toml"
    _write(path, 'api_id = 1\napi_hash = "x"\n')

    real_open = Path.open

    def _raising_open(self: Path, *args: object, **kwargs: object) -> object:
        if self == path:
            raise FileNotFoundError(2, "No such file", str(self))
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", _raising_open)
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path)
    assert "my.telegram.org" in str(exc.value)


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


def test_load_config_api_id_zero_rejected(tmp_path: Path) -> None:
    """``api_id = 0`` would slip past the type check but fail later in
    Telethon with an unactionable error; reject it at config load."""
    _write(tmp_path / "config.toml", 'api_id = 0\napi_hash = "x"\n')
    with pytest.raises(ConfigError, match="api_id.*must be positive"):
        load_config(tmp_path)


def test_load_config_api_id_negative_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "config.toml", 'api_id = -5\napi_hash = "x"\n')
    with pytest.raises(ConfigError, match="api_id.*must be positive"):
        load_config(tmp_path)


def test_load_config_api_hash_empty_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "config.toml", 'api_id = 1\napi_hash = ""\n')
    with pytest.raises(ConfigError, match="api_hash.*non-empty"):
        load_config(tmp_path)


def test_load_config_api_hash_whitespace_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "config.toml", 'api_id = 1\napi_hash = "   "\n')
    with pytest.raises(ConfigError, match="api_hash.*non-empty"):
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
