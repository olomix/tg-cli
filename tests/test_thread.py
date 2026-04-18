"""Tests for the ``tg thread`` command."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner
from telethon.errors import MsgIdInvalidError, PeerIdInvalidError

from tg_cli import cli
from tg_cli.config import ConfigError


class _AsyncIter:
    def __init__(self, items: Iterable[Any]) -> None:
        self._items = list(items)

    def __aiter__(self) -> _AsyncIter:
        return self

    async def __anext__(self) -> Any:
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


def _msg(
    *,
    id: int,
    text: str,
    date: datetime,
    sender_id: int | None = None,
    sender: SimpleNamespace | None = None,
    reply_to_msg_id: int | None = None,
) -> SimpleNamespace:
    reply_to = (
        SimpleNamespace(reply_to_msg_id=reply_to_msg_id)
        if reply_to_msg_id is not None
        else None
    )
    return SimpleNamespace(
        id=id,
        message=text,
        text=text,
        date=date,
        sender=sender,
        sender_id=sender_id,
        reply_to=reply_to,
    )


def _entity(id: int, title: str = "Group") -> SimpleNamespace:
    # Bare positive id + channel flags match real Telethon shape; the
    # command converts this to a marked peer id for ``group_id`` output.
    return SimpleNamespace(id=id, title=title, megagroup=True, broadcast=False)


def _fake_client(
    *,
    entity: SimpleNamespace,
    root: Any,
    replies: Iterable[Any] = (),
    authorized: bool = True,
) -> MagicMock:
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=authorized)
    client.get_entity = AsyncMock(return_value=entity)
    client.get_messages = AsyncMock(return_value=root)
    client.iter_dialogs = MagicMock(return_value=_AsyncIter([]))
    client.iter_messages = MagicMock(
        return_value=_AsyncIter(list(replies))
    )
    return client


def _invoke(client: MagicMock, *args: str) -> Any:
    with patch(
        "tg_cli.commands.thread.make_client", return_value=client
    ):
        return CliRunner().invoke(cli.main, ["thread", *args])


def test_thread_includes_root_first_then_chronological_replies() -> None:
    # Bare channel id ``1234567890`` → marked peer id ``-1001234567890``.
    entity = _entity(1234567890, "Dev")
    sender = SimpleNamespace(
        first_name="Alice", last_name="Doe", username="alice"
    )
    root = _msg(
        id=10,
        text="question",
        date=datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc),
        sender=sender,
        sender_id=42,
    )
    replies = [
        _msg(
            id=11,
            text="first reply",
            date=datetime(2026, 4, 17, 10, 1, tzinfo=timezone.utc),
            sender=sender,
            sender_id=42,
            reply_to_msg_id=10,
        ),
        _msg(
            id=12,
            text="second reply",
            date=datetime(2026, 4, 17, 10, 2, tzinfo=timezone.utc),
            sender=sender,
            sender_id=42,
            reply_to_msg_id=10,
        ),
    ]
    client = _fake_client(entity=entity, root=root, replies=replies)
    result = _invoke(client, "@dev", "10")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert [m["id"] for m in data] == [10, 11, 12]
    assert data[0] == {
        "id": 10,
        "date": "2026-04-17T10:00:00+00:00",
        "sender_id": 42,
        "sender_name": "Alice Doe",
        "text": "question",
        "reply_to_id": None,
        "group_id": -1001234567890,
    }
    assert data[1]["reply_to_id"] == 10
    assert data[2]["reply_to_id"] == 10


def test_thread_passes_reply_to_and_reverse_to_iter_messages() -> None:
    entity = _entity(1)
    root = _msg(
        id=5,
        text="root",
        date=datetime(2026, 4, 17, tzinfo=timezone.utc),
    )
    client = _fake_client(entity=entity, root=root)
    result = _invoke(client, "1", "5")
    assert result.exit_code == 0, result.output
    args, kwargs = client.iter_messages.call_args
    assert args == (entity,)
    assert kwargs["reply_to"] == 5
    assert kwargs["reverse"] is True
    assert kwargs["limit"] == 100


def test_thread_respects_custom_limit() -> None:
    entity = _entity(1)
    root = _msg(
        id=5,
        text="root",
        date=datetime(2026, 4, 17, tzinfo=timezone.utc),
    )
    client = _fake_client(entity=entity, root=root)
    result = _invoke(client, "1", "5", "--limit", "25")
    assert result.exit_code == 0, result.output
    _, kwargs = client.iter_messages.call_args
    assert kwargs["limit"] == 25


def test_thread_with_no_replies_returns_only_root() -> None:
    entity = _entity(1)
    root = _msg(
        id=5,
        text="root",
        date=datetime(2026, 4, 17, tzinfo=timezone.utc),
    )
    client = _fake_client(entity=entity, root=root, replies=[])
    result = _invoke(client, "1", "5")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert len(data) == 1
    assert data[0]["id"] == 5


def test_thread_missing_message_returns_error() -> None:
    entity = _entity(1)
    client = _fake_client(entity=entity, root=None)
    result = _invoke(client, "1", "9999")
    assert result.exit_code != 0
    assert "9999" in result.stderr
    assert "not found" in result.stderr.lower()
    client.iter_messages.assert_not_called()
    client.disconnect.assert_awaited_once()


def test_thread_pretty_indents_json() -> None:
    entity = _entity(1)
    root = _msg(
        id=5,
        text="root",
        date=datetime(2026, 4, 17, tzinfo=timezone.utc),
    )
    client = _fake_client(entity=entity, root=root)
    result = _invoke(client, "1", "5", "--pretty")
    assert result.exit_code == 0, result.output
    assert "\n  " in result.stdout
    assert json.loads(result.stdout)[0]["id"] == 5


def test_thread_errors_when_not_authorized() -> None:
    entity = _entity(1)
    root = _msg(
        id=5,
        text="root",
        date=datetime(2026, 4, 17, tzinfo=timezone.utc),
    )
    client = _fake_client(
        entity=entity, root=root, authorized=False
    )
    result = _invoke(client, "1", "5")
    assert result.exit_code != 0
    assert "Not logged in" in result.stderr
    assert "tg login" in result.stderr
    client.get_messages.assert_not_called()
    client.iter_messages.assert_not_called()
    client.disconnect.assert_awaited_once()


def test_thread_surfaces_config_error() -> None:
    with patch(
        "tg_cli.commands.thread.make_client",
        side_effect=ConfigError("credentials missing"),
    ):
        result = CliRunner().invoke(
            cli.main, ["thread", "1", "5"]
        )
    assert result.exit_code != 0
    assert "credentials missing" in result.stderr


def test_thread_missing_message_id_argument_errors() -> None:
    result = CliRunner().invoke(cli.main, ["thread", "1"])
    assert result.exit_code != 0
    payload = json.loads(result.stderr)
    assert payload["type"] == "UsageError"
    assert "MESSAGE_ID" in payload["error"]


def test_thread_non_integer_message_id_rejected() -> None:
    result = CliRunner().invoke(
        cli.main, ["thread", "1", "not-an-int"]
    )
    assert result.exit_code != 0
    payload = json.loads(result.stderr)
    assert payload["type"] == "UsageError"
    assert "not-an-int" in payload["error"]


def test_thread_resolves_group_by_title_substring() -> None:
    entity = _entity(99, "Cool Group")
    root = _msg(
        id=5,
        text="root",
        date=datetime(2026, 4, 17, tzinfo=timezone.utc),
    )
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=True)
    client.get_entity = AsyncMock()
    client.get_messages = AsyncMock(return_value=root)
    dialog = SimpleNamespace(entity=entity, id=99, name="Cool Group")
    client.iter_dialogs = MagicMock(return_value=_AsyncIter([dialog]))
    client.iter_messages = MagicMock(return_value=_AsyncIter([]))

    with patch(
        "tg_cli.commands.thread.make_client", return_value=client
    ):
        result = CliRunner().invoke(cli.main, ["thread", "cool", "5"])
    assert result.exit_code == 0, result.output
    client.get_entity.assert_not_called()
    client.get_messages.assert_awaited_once_with(entity, ids=5)
    args, _ = client.iter_messages.call_args
    assert args[0] is entity


def test_thread_converts_peer_id_invalid_on_get_messages() -> None:
    """``PeerIdInvalidError`` from Telethon must surface as the
    documented ``MessageNotFoundError`` JSON error, not a traceback."""
    entity = _entity(1)
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=True)
    client.get_entity = AsyncMock(return_value=entity)
    client.iter_dialogs = MagicMock(return_value=_AsyncIter([]))
    client.get_messages = AsyncMock(
        side_effect=PeerIdInvalidError(request=None)
    )
    client.iter_messages = MagicMock(return_value=_AsyncIter([]))
    with patch(
        "tg_cli.commands.thread.make_client", return_value=client
    ):
        result = CliRunner().invoke(cli.main, ["thread", "1", "5"])
    assert result.exit_code != 0
    assert '"type": "MessageNotFoundError"' in result.stderr
    assert "not found" in result.stderr.lower()


def test_thread_converts_msg_id_invalid_on_get_messages() -> None:
    """Telethon raises ``MsgIdInvalidError`` (not just
    ``PeerIdInvalidError``) on ``get_messages`` for nonexistent/invalid
    message ids; it must also surface as ``MessageNotFoundError`` JSON."""
    entity = _entity(1)
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=True)
    client.get_entity = AsyncMock(return_value=entity)
    client.iter_dialogs = MagicMock(return_value=_AsyncIter([]))
    client.get_messages = AsyncMock(
        side_effect=MsgIdInvalidError(request=None)
    )
    client.iter_messages = MagicMock(return_value=_AsyncIter([]))
    with patch(
        "tg_cli.commands.thread.make_client", return_value=client
    ):
        result = CliRunner().invoke(cli.main, ["thread", "1", "5"])
    assert result.exit_code != 0
    assert '"type": "MessageNotFoundError"' in result.stderr
    assert "not found" in result.stderr.lower()
    client.iter_messages.assert_not_called()
    client.disconnect.assert_awaited_once()


def test_thread_converts_msg_id_invalid_on_iter_messages() -> None:
    """When ``reply_to`` targets a chat without discussion threads,
    Telethon raises ``MsgIdInvalidError``; must surface as
    ``MessageNotFoundError`` JSON."""
    entity = _entity(1)
    root = _msg(
        id=5,
        text="root",
        date=datetime(2026, 4, 17, tzinfo=timezone.utc),
    )

    class _ThrowingIter:
        def __aiter__(self) -> _ThrowingIter:
            return self

        async def __anext__(self) -> Any:
            raise MsgIdInvalidError(request=None)

    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=True)
    client.get_entity = AsyncMock(return_value=entity)
    client.iter_dialogs = MagicMock(return_value=_AsyncIter([]))
    client.get_messages = AsyncMock(return_value=root)
    client.iter_messages = MagicMock(return_value=_ThrowingIter())
    with patch(
        "tg_cli.commands.thread.make_client", return_value=client
    ):
        result = CliRunner().invoke(cli.main, ["thread", "1", "5"])
    assert result.exit_code != 0
    assert '"type": "MessageNotFoundError"' in result.stderr
    assert "reply thread" in result.stderr.lower()


def test_thread_disconnects_on_success() -> None:
    entity = _entity(1)
    root = _msg(
        id=5,
        text="root",
        date=datetime(2026, 4, 17, tzinfo=timezone.utc),
    )
    client = _fake_client(entity=entity, root=root)
    result = _invoke(client, "1", "5")
    assert result.exit_code == 0
    client.connect.assert_awaited_once()
    client.disconnect.assert_awaited_once()
