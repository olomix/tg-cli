"""Tests for the ``tg messages`` command and :class:`Message` model."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from tg_cli import cli
from tg_cli.commands import _message as message_mod
from tg_cli.config import ConfigError
from tg_cli.models import Message


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
    return SimpleNamespace(id=id, title=title)


def _fake_client(
    *,
    entity: SimpleNamespace,
    history: Iterable[Any],
    authorized: bool = True,
) -> MagicMock:
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=authorized)
    client.get_entity = AsyncMock(return_value=entity)
    client.iter_dialogs = MagicMock(return_value=_AsyncIter([]))
    client.iter_messages = MagicMock(return_value=_AsyncIter(list(history)))
    return client


def _invoke(client: MagicMock, *args: str) -> Any:
    with patch(
        "tg_cli.commands.messages.make_client", return_value=client
    ):
        return CliRunner().invoke(cli.main, ["messages", *args])


def test_message_to_dict_serialises_date_as_iso_string() -> None:
    m = Message(
        id=1,
        date=datetime(2026, 4, 17, 10, 23, 45, tzinfo=timezone.utc),
        sender_id=42,
        sender_name="Alice",
        text="hello",
        reply_to_id=None,
        group_id=-1001,
    )
    assert m.to_dict() == {
        "id": 1,
        "date": "2026-04-17T10:23:45+00:00",
        "sender_id": 42,
        "sender_name": "Alice",
        "text": "hello",
        "reply_to_id": None,
        "group_id": -1001,
    }


def test_messages_outputs_contract_shape() -> None:
    entity = _entity(-1001234567890, "Dev")
    sender = SimpleNamespace(
        first_name="Alice", last_name="Doe", username="alice"
    )
    # Without ``--since`` the command fetches newest→oldest from
    # Telethon (no ``reverse=True``), so the fake iterator yields in
    # that order; the command reverses to output oldest-first.
    history = [
        _msg(
            id=2,
            text="reply",
            date=datetime(2026, 4, 17, 10, 5, tzinfo=timezone.utc),
            sender=sender,
            sender_id=42,
            reply_to_msg_id=1,
        ),
        _msg(
            id=1,
            text="hello",
            date=datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc),
            sender=sender,
            sender_id=42,
        ),
    ]
    client = _fake_client(entity=entity, history=history)
    result = _invoke(client, "@dev")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data == [
        {
            "id": 1,
            "date": "2026-04-17T10:00:00+00:00",
            "sender_id": 42,
            "sender_name": "Alice Doe",
            "text": "hello",
            "reply_to_id": None,
            "group_id": -1001234567890,
        },
        {
            "id": 2,
            "date": "2026-04-17T10:05:00+00:00",
            "sender_id": 42,
            "sender_name": "Alice Doe",
            "text": "reply",
            "reply_to_id": 1,
            "group_id": -1001234567890,
        },
    ]
    client.get_entity.assert_awaited_once_with("@dev")


def test_messages_without_since_fetches_newest_without_reverse() -> None:
    """No ``--since``: must not pass ``reverse=True`` (that starts at the
    beginning of chat history); should fetch newest messages instead."""
    entity = _entity(1, "Group")
    client = _fake_client(entity=entity, history=[])
    result = _invoke(client, "1", "--limit", "5")
    assert result.exit_code == 0, result.output
    args, kwargs = client.iter_messages.call_args
    assert args == (entity,)
    assert kwargs["limit"] == 5
    assert kwargs.get("reverse") is not True
    assert kwargs.get("offset_date") is None


def test_messages_default_limit_is_100() -> None:
    entity = _entity(1)
    client = _fake_client(entity=entity, history=[])
    result = _invoke(client, "1")
    assert result.exit_code == 0, result.output
    _, kwargs = client.iter_messages.call_args
    assert kwargs["limit"] == 100


def test_messages_since_passed_through_as_utc_datetime_with_reverse() -> None:
    entity = _entity(1)
    client = _fake_client(entity=entity, history=[])
    result = _invoke(client, "1", "--since", "2026-04-15T10:00")
    assert result.exit_code == 0, result.output
    _, kwargs = client.iter_messages.call_args
    assert kwargs["offset_date"] == datetime(
        2026, 4, 15, 10, 0, tzinfo=timezone.utc
    )
    # ``reverse=True`` is required so ``offset_date`` means "newer than".
    assert kwargs["reverse"] is True


def test_messages_without_since_reverses_list_for_oldest_first() -> None:
    """Telethon yields newest→oldest without ``reverse``; command must
    reverse the Python list so output is documented oldest-first."""
    entity = _entity(1)
    history = [
        _msg(
            id=3,
            text="newest",
            date=datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
        ),
        _msg(
            id=2,
            text="middle",
            date=datetime(2026, 4, 17, 11, 0, tzinfo=timezone.utc),
        ),
        _msg(
            id=1,
            text="oldest",
            date=datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc),
        ),
    ]
    client = _fake_client(entity=entity, history=history)
    result = _invoke(client, "1")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert [m["id"] for m in data] == [1, 2, 3]


def test_messages_invalid_since_returns_clean_error() -> None:
    entity = _entity(1)
    client = _fake_client(entity=entity, history=[])
    result = _invoke(client, "1", "--since", "tomorrow")
    assert result.exit_code != 0
    assert "tomorrow" in result.stderr or "tomorrow" in result.output
    client.iter_messages.assert_not_called()


def test_messages_pretty_indents_json() -> None:
    entity = _entity(1, "G")
    history = [
        _msg(
            id=1,
            text="hi",
            date=datetime(2026, 4, 17, tzinfo=timezone.utc),
        )
    ]
    client = _fake_client(entity=entity, history=history)
    result = _invoke(client, "1", "--pretty")
    assert result.exit_code == 0, result.output
    assert "\n  " in result.stdout
    assert json.loads(result.stdout)[0]["id"] == 1


def test_messages_empty_history_returns_empty_array() -> None:
    entity = _entity(1)
    client = _fake_client(entity=entity, history=[])
    result = _invoke(client, "1")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []


def test_messages_errors_when_not_authorized() -> None:
    entity = _entity(1)
    client = _fake_client(entity=entity, history=[], authorized=False)
    result = _invoke(client, "1")
    assert result.exit_code != 0
    assert "Not logged in" in result.stderr
    assert "tg login" in result.stderr
    client.iter_messages.assert_not_called()
    client.disconnect.assert_awaited_once()


def test_messages_surfaces_config_error() -> None:
    with patch(
        "tg_cli.commands.messages.make_client",
        side_effect=ConfigError("credentials missing"),
    ):
        result = CliRunner().invoke(cli.main, ["messages", "1"])
    assert result.exit_code != 0
    assert "credentials missing" in result.stderr


def test_messages_disconnects_on_success() -> None:
    entity = _entity(1)
    client = _fake_client(entity=entity, history=[])
    result = _invoke(client, "1")
    assert result.exit_code == 0
    client.connect.assert_awaited_once()
    client.disconnect.assert_awaited_once()


def test_messages_naive_telethon_date_is_promoted_to_utc() -> None:
    entity = _entity(1)
    history = [
        _msg(id=1, text="hi", date=datetime(2026, 4, 17, 10, 0)),
    ]
    client = _fake_client(entity=entity, history=history)
    result = _invoke(client, "1")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data[0]["date"] == "2026-04-17T10:00:00+00:00"


def test_messages_sender_name_falls_back_to_title_then_username() -> None:
    entity = _entity(1)
    chat_sender = SimpleNamespace(title="Channel Bot")
    user_sender = SimpleNamespace(
        first_name=None, last_name=None, username="onlyhandle"
    )
    # Fake iterator yields newest→oldest (Telethon default without
    # ``--since``); the command reverses for oldest-first output, so
    # index 0 of the JSON corresponds to the last entry here.
    history = [
        _msg(
            id=2,
            text="from user",
            date=datetime(2026, 4, 17, tzinfo=timezone.utc),
            sender=user_sender,
            sender_id=20,
        ),
        _msg(
            id=1,
            text="from chat",
            date=datetime(2026, 4, 17, tzinfo=timezone.utc),
            sender=chat_sender,
            sender_id=10,
        ),
    ]
    client = _fake_client(entity=entity, history=history)
    result = _invoke(client, "1")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data[0]["sender_name"] == "Channel Bot"
    assert data[1]["sender_name"] == "onlyhandle"


def test_messages_sender_name_is_none_when_sender_missing() -> None:
    entity = _entity(1)
    history = [
        _msg(
            id=1,
            text="anon",
            date=datetime(2026, 4, 17, tzinfo=timezone.utc),
        ),
    ]
    client = _fake_client(entity=entity, history=history)
    result = _invoke(client, "1")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data[0]["sender_name"] is None
    assert data[0]["sender_id"] is None


def test_messages_resolves_group_by_title_substring() -> None:
    entity = _entity(99, "Cool Group")
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=True)
    client.get_entity = AsyncMock()
    dialog = SimpleNamespace(entity=entity, id=99, name="Cool Group")
    client.iter_dialogs = MagicMock(return_value=_AsyncIter([dialog]))
    client.iter_messages = MagicMock(return_value=_AsyncIter([]))

    with patch(
        "tg_cli.commands.messages.make_client", return_value=client
    ):
        result = CliRunner().invoke(cli.main, ["messages", "cool"])
    assert result.exit_code == 0, result.output
    # Title-based resolution: get_entity is *not* called (substring match).
    client.get_entity.assert_not_called()
    args, _ = client.iter_messages.call_args
    assert args[0] is entity


def test_messages_ambiguous_group_returns_clean_error() -> None:
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=True)
    client.get_entity = AsyncMock()
    dialogs = [
        SimpleNamespace(
            entity=_entity(1, "Dev Frontend"), id=1, name="Dev Frontend"
        ),
        SimpleNamespace(
            entity=_entity(2, "Dev Backend"), id=2, name="Dev Backend"
        ),
    ]
    client.iter_dialogs = MagicMock(return_value=_AsyncIter(dialogs))
    client.iter_messages = MagicMock(return_value=_AsyncIter([]))

    with patch(
        "tg_cli.commands.messages.make_client", return_value=client
    ):
        result = CliRunner().invoke(cli.main, ["messages", "dev"])
    assert result.exit_code != 0
    assert "ambiguous" in result.stderr.lower()
    client.iter_messages.assert_not_called()


def test_to_message_helper_handles_missing_attributes() -> None:
    raw = SimpleNamespace(
        id=7,
        message=None,
        text=None,
        date=datetime(2026, 4, 17, tzinfo=timezone.utc),
        sender=None,
        sender_id=None,
        reply_to=None,
    )
    msg = message_mod.to_message(raw, group_id=42)
    assert msg.text == ""
    assert msg.sender_id is None
    assert msg.sender_name is None
    assert msg.reply_to_id is None
    assert msg.group_id == 42
