"""Tests for the ``tg search`` command."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

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
        "tg_cli.commands.search.make_client", return_value=client
    ):
        return CliRunner().invoke(cli.main, ["search", *args])


def test_search_passes_query_to_iter_messages() -> None:
    entity = _entity(1, "Group")
    client = _fake_client(entity=entity, history=[])
    result = _invoke(client, "1", "hello world")
    assert result.exit_code == 0, result.output
    args, kwargs = client.iter_messages.call_args
    assert args == (entity,)
    assert kwargs["search"] == "hello world"
    assert kwargs["limit"] == 100
    assert kwargs["offset_date"] is None
    # No ``--since``: Telethon's default newest-first iteration is fine.
    assert kwargs.get("reverse") is not True


def test_search_passes_since_with_reverse_for_newer_than_semantics() -> None:
    """``offset_date`` in Telethon means "messages older than X" by
    default; ``reverse=True`` flips it to "newer than X", which is what
    ``--since`` documents."""
    entity = _entity(1)
    client = _fake_client(entity=entity, history=[])
    result = _invoke(
        client, "1", "query", "--since", "2026-04-15T10:00"
    )
    assert result.exit_code == 0, result.output
    _, kwargs = client.iter_messages.call_args
    assert kwargs["offset_date"] == datetime(
        2026, 4, 15, 10, 0, tzinfo=timezone.utc
    )
    assert kwargs["search"] == "query"
    assert kwargs["reverse"] is True


def test_search_with_since_reverses_list_for_newest_first_output() -> None:
    """With ``reverse=True`` Telethon yields oldest→newest; the command
    reverses the list so the documented newest-first ordering holds."""
    entity = _entity(1)
    history = [
        _msg(
            id=1,
            text="match",
            date=datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc),
        ),
        _msg(
            id=2,
            text="match",
            date=datetime(2026, 4, 17, 11, 0, tzinfo=timezone.utc),
        ),
        _msg(
            id=3,
            text="match",
            date=datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
        ),
    ]
    client = _fake_client(entity=entity, history=history)
    result = _invoke(client, "1", "match", "--since", "24h")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert [m["id"] for m in data] == [3, 2, 1]


def test_search_respects_custom_limit() -> None:
    entity = _entity(1)
    client = _fake_client(entity=entity, history=[])
    result = _invoke(client, "1", "q", "--limit", "25")
    assert result.exit_code == 0, result.output
    _, kwargs = client.iter_messages.call_args
    assert kwargs["limit"] == 25


def test_search_output_matches_messages_shape() -> None:
    entity = _entity(-1001234567890, "Dev")
    sender = SimpleNamespace(
        first_name="Alice", last_name="Doe", username="alice"
    )
    history = [
        _msg(
            id=7,
            text="hello world",
            date=datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc),
            sender=sender,
            sender_id=42,
        ),
    ]
    client = _fake_client(entity=entity, history=history)
    result = _invoke(client, "@dev", "hello")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data == [
        {
            "id": 7,
            "date": "2026-04-17T10:00:00+00:00",
            "sender_id": 42,
            "sender_name": "Alice Doe",
            "text": "hello world",
            "reply_to_id": None,
            "group_id": -1001234567890,
        }
    ]


def test_search_empty_result_returns_empty_array() -> None:
    entity = _entity(1)
    client = _fake_client(entity=entity, history=[])
    result = _invoke(client, "1", "nothing matches")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []


def test_search_pretty_indents_json() -> None:
    entity = _entity(1)
    history = [
        _msg(
            id=1,
            text="match",
            date=datetime(2026, 4, 17, tzinfo=timezone.utc),
        )
    ]
    client = _fake_client(entity=entity, history=history)
    result = _invoke(client, "1", "match", "--pretty")
    assert result.exit_code == 0, result.output
    assert "\n  " in result.stdout
    assert json.loads(result.stdout)[0]["id"] == 1


def test_search_errors_when_not_authorized() -> None:
    entity = _entity(1)
    client = _fake_client(entity=entity, history=[], authorized=False)
    result = _invoke(client, "1", "q")
    assert result.exit_code != 0
    assert "Not logged in" in result.stderr
    assert "tg login" in result.stderr
    client.iter_messages.assert_not_called()
    client.disconnect.assert_awaited_once()


def test_search_invalid_since_returns_clean_error() -> None:
    entity = _entity(1)
    client = _fake_client(entity=entity, history=[])
    result = _invoke(client, "1", "q", "--since", "tomorrow")
    assert result.exit_code != 0
    assert "tomorrow" in result.stderr or "tomorrow" in result.output
    client.iter_messages.assert_not_called()


def test_search_surfaces_config_error() -> None:
    with patch(
        "tg_cli.commands.search.make_client",
        side_effect=ConfigError("credentials missing"),
    ):
        result = CliRunner().invoke(cli.main, ["search", "1", "q"])
    assert result.exit_code != 0
    assert "credentials missing" in result.stderr


def test_search_missing_query_argument_errors() -> None:
    result = CliRunner().invoke(cli.main, ["search", "1"])
    assert result.exit_code != 0
    assert "QUERY" in result.output or "query" in result.output.lower()


def test_search_resolves_group_by_title_substring() -> None:
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
        "tg_cli.commands.search.make_client", return_value=client
    ):
        result = CliRunner().invoke(cli.main, ["search", "cool", "q"])
    assert result.exit_code == 0, result.output
    client.get_entity.assert_not_called()
    args, kwargs = client.iter_messages.call_args
    assert args[0] is entity
    assert kwargs["search"] == "q"


def test_search_disconnects_on_success() -> None:
    entity = _entity(1)
    client = _fake_client(entity=entity, history=[])
    result = _invoke(client, "1", "q")
    assert result.exit_code == 0
    client.connect.assert_awaited_once()
    client.disconnect.assert_awaited_once()
