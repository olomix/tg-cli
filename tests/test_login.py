"""Tests for the ``tg login`` command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner
from telethon.errors import (
    PasswordHashInvalidError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

from tg_cli import cli
from tg_cli.commands.login import _tighten_session_perms
from tg_cli.config import ConfigError


def _fake_client(
    *, authorized: bool = False, needs_2fa: bool = False
) -> MagicMock:
    client = MagicMock()
    client.connect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=authorized)
    client.send_code_request = AsyncMock()
    if needs_2fa:
        client.sign_in = AsyncMock(
            side_effect=[
                SessionPasswordNeededError(request=None),
                None,
            ]
        )
    else:
        client.sign_in = AsyncMock()
    client.disconnect = AsyncMock()
    return client


def test_login_noop_when_already_authorized() -> None:
    client = _fake_client(authorized=True)
    with patch("tg_cli.commands.login.make_client", return_value=client):
        result = CliRunner().invoke(cli.main, ["login"])
    assert result.exit_code == 0, result.output
    assert "Already logged in" in result.output
    client.send_code_request.assert_not_called()
    client.sign_in.assert_not_called()
    client.disconnect.assert_awaited_once()


def test_login_with_phone_flag_sends_code_and_signs_in() -> None:
    client = _fake_client()
    with patch("tg_cli.commands.login.make_client", return_value=client):
        result = CliRunner().invoke(
            cli.main,
            ["login", "--phone", "+15551234567"],
            input="98765\n",
        )
    assert result.exit_code == 0, result.output
    client.connect.assert_awaited_once()
    client.send_code_request.assert_awaited_once_with("+15551234567")
    client.sign_in.assert_awaited_once_with(
        phone="+15551234567", code="98765"
    )
    client.disconnect.assert_awaited_once()
    assert "Login successful" in result.output


def test_login_prompts_for_phone_when_flag_absent() -> None:
    client = _fake_client()
    with patch("tg_cli.commands.login.make_client", return_value=client):
        result = CliRunner().invoke(
            cli.main,
            ["login"],
            input="+15551234567\n98765\n",
        )
    assert result.exit_code == 0, result.output
    client.send_code_request.assert_awaited_once_with("+15551234567")
    client.sign_in.assert_awaited_once_with(
        phone="+15551234567", code="98765"
    )


def test_login_handles_2fa_password() -> None:
    client = _fake_client(needs_2fa=True)
    with patch("tg_cli.commands.login.make_client", return_value=client):
        result = CliRunner().invoke(
            cli.main,
            ["login", "--phone", "+15551234567"],
            input="98765\nmypassword\n",
        )
    assert result.exit_code == 0, result.output
    assert client.sign_in.await_count == 2
    first_call, second_call = client.sign_in.await_args_list
    assert first_call.kwargs == {
        "phone": "+15551234567",
        "code": "98765",
    }
    assert second_call.kwargs == {"password": "mypassword"}
    assert "Login successful" in result.output


def test_login_surfaces_config_error() -> None:
    with patch(
        "tg_cli.commands.login.make_client",
        side_effect=ConfigError("credentials missing; see README"),
    ):
        result = CliRunner().invoke(cli.main, ["login"])
    assert result.exit_code != 0
    assert "credentials missing" in result.output


def test_login_disconnects_even_on_error() -> None:
    client = _fake_client()
    client.send_code_request.side_effect = RuntimeError("boom")
    with patch("tg_cli.commands.login.make_client", return_value=client):
        result = CliRunner().invoke(
            cli.main,
            ["login", "--phone", "+15551234567"],
            input="98765\n",
        )
    assert result.exit_code != 0
    client.disconnect.assert_awaited_once()


def test_login_invalid_code_emits_json_error() -> None:
    """Wrong login code must emit a structured JSON error, not leak a
    traceback — Claude relies on the error contract for login failures."""
    client = _fake_client()
    client.sign_in = AsyncMock(side_effect=PhoneCodeInvalidError(request=None))
    with patch("tg_cli.commands.login.make_client", return_value=client):
        result = CliRunner().invoke(
            cli.main,
            ["login", "--phone", "+15551234567"],
            input="00000\n",
        )
    assert result.exit_code != 0
    assert '"type": "AuthError"' in result.stderr
    assert "login code" in result.stderr.lower()
    client.disconnect.assert_awaited_once()


def test_tighten_session_perms_chmods_sidecars(tmp_path: Path) -> None:
    """SQLite can spill session secrets into ``-wal``/``-shm`` sidecars
    when WAL mode is active; all three files must be locked to 0o600,
    otherwise tightening the main ``.session`` file is a false sense
    of security."""
    session_base = tmp_path / "session"
    sqlite_path = tmp_path / "session.session"
    wal_path = tmp_path / "session.session-wal"
    shm_path = tmp_path / "session.session-shm"
    for p in (sqlite_path, wal_path, shm_path):
        p.write_bytes(b"x")
        p.chmod(0o644)

    _tighten_session_perms(session_base)

    for p in (sqlite_path, wal_path, shm_path):
        assert p.stat().st_mode & 0o777 == 0o600, p


def test_tighten_session_perms_skips_missing_sidecars(tmp_path: Path) -> None:
    """WAL mode is optional; when sidecars are absent the chmod loop
    must silently skip them rather than raising."""
    session_base = tmp_path / "session"
    sqlite_path = tmp_path / "session.session"
    sqlite_path.write_bytes(b"x")
    sqlite_path.chmod(0o644)

    _tighten_session_perms(session_base)

    assert sqlite_path.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "session.session-wal").exists()
    assert not (tmp_path / "session.session-shm").exists()


def test_login_invalid_2fa_password_emits_json_error() -> None:
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=False)
    client.send_code_request = AsyncMock()
    client.sign_in = AsyncMock(
        side_effect=[
            SessionPasswordNeededError(request=None),
            PasswordHashInvalidError(request=None),
        ]
    )
    with patch("tg_cli.commands.login.make_client", return_value=client):
        result = CliRunner().invoke(
            cli.main,
            ["login", "--phone", "+15551234567"],
            input="98765\nwrongpw\n",
        )
    assert result.exit_code != 0
    assert '"type": "AuthError"' in result.stderr
    assert "2fa" in result.stderr.lower()
    client.disconnect.assert_awaited_once()
