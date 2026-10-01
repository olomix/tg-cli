"""Tests for standardised JSON error output across commands.

Every command must emit ``{"error": "...", "type": "..."}`` to stderr
and exit non-zero for all recognised failure modes. These tests are
parametrised over the six data commands (``groups``/``messages``/
``search``/``thread``/``get``/``download``) since they share the same
decorator and exception surface.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeInvalidError,
    RPCError,
    SessionPasswordNeededError,
)

from tg_cli import cli
from tg_cli.commands._resolve import (
    AmbiguousGroupError,
    GroupNotFoundError,
)
from tg_cli.commands._time import TimeParseError
from tg_cli.config import ConfigError
from tg_cli.errors import (
    NOT_LOGGED_IN_MESSAGE,
    AuthError,
    DownloadError,
    MessageNotFoundError,
    _classify,
    emit_error,
    handle_errors,
)


def _parse_error(stderr: str) -> dict[str, Any]:
    """Return the parsed JSON error object from a stderr blob.

    Commands may only emit a single JSON line to stderr on failure.
    The test helper strips trailing whitespace and ensures there's
    exactly one JSON payload so tests can assert on shape.
    """
    lines = [line for line in stderr.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected one stderr line, got: {stderr!r}"
    return json.loads(lines[0])


def test_emit_error_writes_json_object_to_stderr() -> None:
    import click

    @click.command()
    def raiser() -> None:
        emit_error("boom", "DemoError")
        raise click.exceptions.Exit(1)

    result = CliRunner().invoke(raiser, [])
    assert result.exit_code == 1
    payload = _parse_error(result.stderr)
    assert payload == {"error": "boom", "type": "DemoError"}


def test_classify_returns_none_for_unknown_exception() -> None:
    assert _classify(RuntimeError("unknown")) is None


def test_classify_maps_known_exception_types() -> None:
    assert _classify(AuthError())[1] == "AuthError"
    assert _classify(AuthError("custom msg")) == ("custom msg", "AuthError")
    assert _classify(AuthError())[0] == NOT_LOGGED_IN_MESSAGE
    assert _classify(MessageNotFoundError("missing"))[1] == (
        "MessageNotFoundError"
    )
    assert _classify(ConfigError("bad"))[1] == "ConfigError"
    assert _classify(TimeParseError("bad time"))[1] == "TimeParseError"
    assert _classify(GroupNotFoundError("no group"))[1] == (
        "GroupNotFoundError"
    )
    assert _classify(AmbiguousGroupError("dev", ["A", "B"]))[1] == (
        "AmbiguousGroupError"
    )
    flood = FloodWaitError(request=None, capture=42)
    msg, typ = _classify(flood)
    assert typ == "FloodWaitError"
    assert "42" in msg
    sess = SessionPasswordNeededError(request=None)
    msg, typ = _classify(sess)
    assert typ == "AuthError"
    assert "tg login" in msg
    # Invalid code / 2FA password from interactive ``tg login`` flow.
    msg, typ = _classify(PhoneCodeInvalidError(request=None))
    assert typ == "AuthError"
    assert "login code" in msg.lower()
    msg, typ = _classify(PasswordHashInvalidError(request=None))
    assert typ == "AuthError"
    assert "2fa" in msg.lower()


def test_classify_wraps_generic_rpc_error_as_telegram_error() -> None:
    """Any unmapped Telethon ``RPCError`` must surface as structured
    JSON rather than a raw traceback."""

    class _SomeRPCError(RPCError):
        def __init__(self) -> None:
            super().__init__(request=None, message="something went wrong")

    msg, typ = _classify(_SomeRPCError())
    assert typ == "TelegramError"
    assert msg


def test_handle_errors_passes_through_unknown_exceptions() -> None:
    """Non-recognised errors must propagate so bugs are visible."""
    import click

    @click.command()
    @handle_errors
    def cmd() -> None:
        raise ValueError("unexpected")

    result = CliRunner().invoke(cmd, [])
    # CliRunner traps exceptions; verify it wasn't silently swallowed
    # into our JSON format.
    assert isinstance(result.exception, ValueError)
    assert result.stderr == ""


def test_classify_maps_download_error() -> None:
    assert _classify(DownloadError("disk full")) == (
        "disk full",
        "DownloadError",
    )


def test_handle_errors_maps_download_error_to_json() -> None:
    import click

    @click.command()
    @handle_errors
    def cmd() -> None:
        raise DownloadError("cannot write photo: disk full")

    result = CliRunner().invoke(cmd, [])
    assert result.exit_code == 1
    payload = _parse_error(result.stderr)
    assert payload == {
        "error": "cannot write photo: disk full",
        "type": "DownloadError",
    }


DATA_COMMANDS = [
    ("groups", ("groups",)),
    ("messages", ("messages", "1")),
    ("search", ("search", "1", "q")),
    ("thread", ("thread", "1", "5")),
    ("get", ("get", "1", "5")),
    ("download", ("download", "--dir", "photos", "1", "5")),
]


@pytest.fixture(autouse=True)
def _scratch_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # ``tg download`` creates its relative ``--dir`` before it connects.
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize("module,argv", DATA_COMMANDS)
def test_config_error_emits_json(module: str, argv: tuple[str, ...]) -> None:
    with patch(
        f"tg_cli.commands.{module}.make_client",
        side_effect=ConfigError("credentials missing; see README"),
    ):
        result = CliRunner().invoke(cli.main, list(argv))
    assert result.exit_code != 0
    payload = _parse_error(result.stderr)
    assert payload["type"] == "ConfigError"
    assert "credentials missing" in payload["error"]


@pytest.mark.parametrize("module,argv", DATA_COMMANDS)
def test_auth_error_emits_json(module: str, argv: tuple[str, ...]) -> None:
    client = _unauthorised_client()
    with patch(
        f"tg_cli.commands.{module}.make_client", return_value=client
    ):
        result = CliRunner().invoke(cli.main, list(argv))
    assert result.exit_code != 0
    payload = _parse_error(result.stderr)
    assert payload["type"] == "AuthError"
    assert "tg login" in payload["error"]


@pytest.mark.parametrize("module,argv", DATA_COMMANDS)
def test_flood_wait_error_emits_json(
    module: str, argv: tuple[str, ...]
) -> None:
    client = _authorised_client()
    # The command path that always runs after auth differs per command;
    # raise from is_user_authorized to trigger a uniform exit point.
    client.is_user_authorized = AsyncMock(
        side_effect=FloodWaitError(request=None, capture=15)
    )
    with patch(
        f"tg_cli.commands.{module}.make_client", return_value=client
    ):
        result = CliRunner().invoke(cli.main, list(argv))
    assert result.exit_code != 0
    payload = _parse_error(result.stderr)
    assert payload["type"] == "FloodWaitError"
    assert "15" in payload["error"]
    assert "flood wait" in payload["error"].lower()


@pytest.mark.parametrize("module,argv", DATA_COMMANDS)
def test_session_password_needed_emits_auth_error(
    module: str, argv: tuple[str, ...]
) -> None:
    """Non-login commands surface 2FA-needed as an AuthError with login hint."""
    client = _authorised_client()
    client.is_user_authorized = AsyncMock(
        side_effect=SessionPasswordNeededError(request=None)
    )
    with patch(
        f"tg_cli.commands.{module}.make_client", return_value=client
    ):
        result = CliRunner().invoke(cli.main, list(argv))
    assert result.exit_code != 0
    payload = _parse_error(result.stderr)
    assert payload["type"] == "AuthError"
    assert "tg login" in payload["error"]


@pytest.mark.parametrize(
    "module,argv",
    [
        ("messages", ("messages", "1", "--since", "tomorrow")),
        ("search", ("search", "1", "q", "--since", "tomorrow")),
    ],
)
def test_time_parse_error_emits_json(
    module: str, argv: tuple[str, ...]
) -> None:
    client = _authorised_client()
    with patch(
        f"tg_cli.commands.{module}.make_client", return_value=client
    ):
        result = CliRunner().invoke(cli.main, list(argv))
    assert result.exit_code != 0
    payload = _parse_error(result.stderr)
    assert payload["type"] == "TimeParseError"
    assert "tomorrow" in payload["error"]


@pytest.mark.parametrize(
    "module,argv",
    [
        ("messages", ("messages", "dev")),
        ("search", ("search", "dev", "q")),
        ("thread", ("thread", "dev", "5")),
    ],
)
def test_ambiguous_group_emits_json(
    module: str, argv: tuple[str, ...]
) -> None:
    from types import SimpleNamespace

    dialogs = [
        SimpleNamespace(
            entity=SimpleNamespace(id=1, title="Dev Frontend"),
            id=1,
            name="Dev Frontend",
        ),
        SimpleNamespace(
            entity=SimpleNamespace(id=2, title="Dev Backend"),
            id=2,
            name="Dev Backend",
        ),
    ]
    client = _authorised_client()
    client.iter_dialogs = MagicMock(return_value=_AsyncIter(dialogs))
    # thread command reaches get_messages only after resolve; make it
    # available so failure comes from resolve, not a missing mock.
    client.get_messages = AsyncMock()
    with patch(
        f"tg_cli.commands.{module}.make_client", return_value=client
    ):
        result = CliRunner().invoke(cli.main, list(argv))
    assert result.exit_code != 0
    payload = _parse_error(result.stderr)
    assert payload["type"] == "AmbiguousGroupError"
    assert "ambiguous" in payload["error"].lower()


def test_thread_missing_message_emits_json() -> None:
    from types import SimpleNamespace

    entity = SimpleNamespace(id=1, title="Group")
    client = _authorised_client()
    client.get_entity = AsyncMock(return_value=entity)
    client.get_messages = AsyncMock(return_value=None)
    client.iter_dialogs = MagicMock(return_value=_AsyncIter([]))
    client.iter_messages = MagicMock(return_value=_AsyncIter([]))
    with patch(
        "tg_cli.commands.thread.make_client", return_value=client
    ):
        result = CliRunner().invoke(
            cli.main, ["thread", "1", "9999"]
        )
    assert result.exit_code != 0
    payload = _parse_error(result.stderr)
    assert payload["type"] == "MessageNotFoundError"
    assert "9999" in payload["error"]
    assert "not found" in payload["error"].lower()


# --- Click parse/usage failures are covered by the JSON contract too
# (see ``_JsonErrorGroup`` in ``cli.py``). Regression for Codex review.

@pytest.mark.parametrize(
    "argv,needle",
    [
        # Unknown subcommand.
        (["bogus"], "bogus"),
        # Missing required argument.
        (["messages"], "GROUP"),
        (["search", "g"], "QUERY"),
        (["thread", "g"], "MESSAGE_ID"),
        # Bad argument type.
        (["thread", "g", "not-an-int"], "not-an-int"),
        # Invalid option value.
        (["groups", "--type", "nonsense"], "nonsense"),
        (["groups", "--limit", "0"], "0"),
    ],
)
def test_click_usage_errors_emit_json(
    argv: list[str], needle: str
) -> None:
    result = CliRunner().invoke(cli.main, argv)
    assert result.exit_code != 0
    payload = _parse_error(result.stderr)
    assert payload["type"] == "UsageError"
    assert needle in payload["error"]


def test_help_flag_still_prints_plain_text() -> None:
    """``--help`` must keep Click's plain-text output (not JSON)."""
    result = CliRunner().invoke(cli.main, ["--help"])
    assert result.exit_code == 0
    # Help goes to stdout; no JSON error on stderr.
    assert result.stderr == ""
    assert "Usage:" in result.stdout


def test_login_config_error_emits_json() -> None:
    with patch(
        "tg_cli.commands.login.make_client",
        side_effect=ConfigError("credentials missing; see README"),
    ):
        result = CliRunner().invoke(cli.main, ["login"])
    assert result.exit_code != 0
    payload = _parse_error(result.stderr)
    assert payload["type"] == "ConfigError"
    assert "credentials missing" in payload["error"]


def test_login_flood_wait_emits_json() -> None:
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(
        side_effect=FloodWaitError(request=None, capture=30)
    )
    with patch("tg_cli.commands.login.make_client", return_value=client):
        result = CliRunner().invoke(cli.main, ["login"])
    assert result.exit_code != 0
    payload = _parse_error(result.stderr)
    assert payload["type"] == "FloodWaitError"
    assert "30" in payload["error"]


def test_login_still_handles_2fa_inline_not_as_error() -> None:
    """login's own 2FA flow must not be caught by the error decorator."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=False)
    client.send_code_request = AsyncMock()
    # First sign_in raises 2FA; second sign_in (with password) succeeds.
    client.sign_in = AsyncMock(
        side_effect=[
            SessionPasswordNeededError(request=None),
            None,
        ]
    )
    with patch("tg_cli.commands.login.make_client", return_value=client):
        result = CliRunner().invoke(
            cli.main,
            ["login", "--phone", "+15551234567"],
            input="98765\nmypassword\n",
        )
    assert result.exit_code == 0, result.stderr
    # No JSON error payload on stderr — login completed normally.
    assert '"error":' not in result.stderr
    assert '"type":' not in result.stderr


# --- helpers -------------------------------------------------------------


class _AsyncIter:
    def __init__(self, items: Any) -> None:
        self._items = list(items)

    def __aiter__(self) -> _AsyncIter:
        return self

    async def __anext__(self) -> Any:
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


def _unauthorised_client() -> MagicMock:
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=False)
    client.iter_dialogs = MagicMock(return_value=_AsyncIter([]))
    client.iter_messages = MagicMock(return_value=_AsyncIter([]))
    client.get_entity = AsyncMock()
    client.get_messages = AsyncMock()
    return client


def _authorised_client() -> MagicMock:
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=True)
    client.iter_dialogs = MagicMock(return_value=_AsyncIter([]))
    client.iter_messages = MagicMock(return_value=_AsyncIter([]))
    client.get_entity = AsyncMock()
    client.get_messages = AsyncMock()
    return client
