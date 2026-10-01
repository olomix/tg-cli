"""Tests for the ``tg messages`` command and :class:`Message` model."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
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
    # Bare positive id shape matches real Telethon ``Channel``; the
    # command code converts it to the marked peer id for ``group_id``.
    return SimpleNamespace(id=id, title=title, megagroup=True, broadcast=False)


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
        "sender_username": None,
        "topic_id": None,
        "media_kind": None,
        "grouped_id": None,
        "urls": [],
        "forward": None,
        "link": None,
    }


def _message_with_old_arguments_only() -> Message:
    return Message(
        id=1,
        date=datetime(2026, 4, 17, 10, 23, 45, tzinfo=timezone.utc),
        sender_id=42,
        sender_name="Alice",
        text="hello",
        reply_to_id=None,
        group_id=-1001,
    )


def test_message_to_dict_appends_new_keys_after_the_old_ones() -> None:
    data = _message_with_old_arguments_only().to_dict()
    assert list(data) == [
        "id",
        "date",
        "sender_id",
        "sender_name",
        "text",
        "reply_to_id",
        "group_id",
        "sender_username",
        "topic_id",
        "media_kind",
        "grouped_id",
        "urls",
        "forward",
        "link",
    ]


def test_message_new_fields_default_to_absent() -> None:
    data = _message_with_old_arguments_only().to_dict()
    assert data["sender_username"] is None
    assert data["topic_id"] is None
    assert data["media_kind"] is None
    assert data["grouped_id"] is None
    assert data["urls"] == []
    assert data["forward"] is None
    assert data["link"] is None


def test_message_to_dict_serialises_new_fields_unchanged() -> None:
    forward = {
        "from_id": -1009876543210,
        "from_name": "Origin",
        "date": "2026-04-16T08:00:00+00:00",
    }
    m = Message(
        id=7,
        date=datetime(2026, 4, 17, 10, 23, 45, tzinfo=timezone.utc),
        sender_id=42,
        sender_name="Alice",
        text="look https://example.com",
        reply_to_id=5,
        group_id=-1001,
        sender_username="alice",
        topic_id=3,
        media_kind="photo",
        grouped_id=13579,
        urls=["https://example.com", "https://example.org/a"],
        forward=forward,
        link="https://t.me/dev/3/7",
    )
    data = m.to_dict()
    assert data["sender_username"] == "alice"
    assert data["topic_id"] == 3
    assert data["media_kind"] == "photo"
    assert data["grouped_id"] == 13579
    assert data["urls"] == ["https://example.com", "https://example.org/a"]
    assert data["forward"] == forward
    assert data["link"] == "https://t.me/dev/3/7"


def test_message_instances_do_not_share_one_urls_list() -> None:
    first = _message_with_old_arguments_only()
    second = _message_with_old_arguments_only()
    assert first.urls == []
    assert first.urls is not second.urls


def test_messages_outputs_contract_shape() -> None:
    # Bare channel id ``1234567890`` → marked peer id ``-1001234567890``
    # in the output per the documented JSON contract.
    entity = _entity(1234567890, "Dev")
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
            "sender_username": "alice",
            "topic_id": None,
            "media_kind": None,
            "grouped_id": None,
            "urls": [],
            "forward": None,
            "link": "https://t.me/c/1234567890/1",
        },
        {
            "id": 2,
            "date": "2026-04-17T10:05:00+00:00",
            "sender_id": 42,
            "sender_name": "Alice Doe",
            "text": "reply",
            "reply_to_id": 1,
            "group_id": -1001234567890,
            "sender_username": "alice",
            "topic_id": None,
            "media_kind": None,
            "grouped_id": None,
            "urls": [],
            "forward": None,
            "link": "https://t.me/c/1234567890/2",
        },
    ]
    client.get_entity.assert_awaited_once_with("@dev")


def test_messages_on_public_group_emits_username_links() -> None:
    entity = SimpleNamespace(
        id=1234567890, title="Dev", megagroup=True, username="dev_chat"
    )
    history = [
        _msg(
            id=2,
            text="reply",
            date=datetime(2026, 4, 17, 10, 5, tzinfo=timezone.utc),
        ),
        _msg(
            id=1,
            text="hello",
            date=datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc),
        ),
    ]
    client = _fake_client(entity=entity, history=history)
    result = _invoke(client, "@dev_chat")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert [m["link"] for m in data] == [
        "https://t.me/dev_chat/1",
        "https://t.me/dev_chat/2",
    ]


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


def test_messages_since_iterates_newest_first_and_filters_client_side() -> (
    None
):
    """``--since`` must cap to the newest messages within the window,
    not the earliest. We iterate newest-first with no ``offset_date``/
    ``reverse``, then break on the cutoff ourselves."""
    entity = _entity(1)
    client = _fake_client(entity=entity, history=[])
    result = _invoke(client, "1", "--since", "2026-04-15T10:00")
    assert result.exit_code == 0, result.output
    _, kwargs = client.iter_messages.call_args
    assert kwargs.get("offset_date") is None
    assert kwargs.get("reverse") is not True


def test_messages_since_returns_newest_messages_when_limit_exceeded() -> None:
    """Before the fix, Telethon's ``reverse=True`` + ``offset_date`` +
    ``limit`` returned the OLDEST N messages after the cutoff, silently
    dropping the most recent activity. Regression test: the command
    must iterate newest-first with ``limit`` passed through, so
    Telethon yields the newest N that Claude actually wants."""
    entity = _entity(1)
    # Simulate Telethon respecting ``limit=2`` with newest-first
    # iteration: only the 2 newest messages reach the CLI.
    history = [
        _msg(
            id=5,
            text="newest",
            date=datetime(2026, 4, 17, 14, 0, tzinfo=timezone.utc),
        ),
        _msg(
            id=4,
            text="second-newest",
            date=datetime(2026, 4, 17, 13, 0, tzinfo=timezone.utc),
        ),
    ]
    client = _fake_client(entity=entity, history=history)
    result = _invoke(
        client, "1", "--since", "2026-04-17T09:00", "--limit", "2"
    )
    assert result.exit_code == 0, result.output
    _, kwargs = client.iter_messages.call_args
    # Key regression assertion: ``limit`` is passed to Telethon directly
    # and no ``offset_date``/``reverse`` is used (those would flip the
    # semantic to "oldest N after cutoff").
    assert kwargs["limit"] == 2
    assert kwargs.get("offset_date") is None
    assert kwargs.get("reverse") is not True
    # Output is oldest-first per documented contract.
    data = json.loads(result.stdout)
    assert [m["id"] for m in data] == [4, 5]


def test_messages_since_stops_iterating_at_cutoff() -> None:
    """Messages older than ``--since`` must be dropped client-side."""
    entity = _entity(1)
    history = [
        _msg(
            id=3,
            text="in window",
            date=datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
        ),
        _msg(
            id=2,
            text="in window",
            date=datetime(2026, 4, 17, 11, 0, tzinfo=timezone.utc),
        ),
        _msg(
            id=1,
            text="too old",
            date=datetime(2026, 4, 17, 9, 0, tzinfo=timezone.utc),
        ),
    ]
    client = _fake_client(entity=entity, history=history)
    result = _invoke(client, "1", "--since", "2026-04-17T10:00")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert [m["id"] for m in data] == [2, 3]


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


_DAY = datetime(2026, 4, 17, tzinfo=timezone.utc)


def _history(*ids: int) -> list[SimpleNamespace]:
    return [_msg(id=i, text=f"m{i}", date=_DAY) for i in ids]


def _client_filtering_by_id(
    entity: SimpleNamespace, ids: Iterable[int]
) -> MagicMock:
    """Client whose ``iter_messages`` applies Telethon's id bounds: both
    exclusive, ``max_id=0`` meaning no upper bound."""
    stored = sorted(ids)

    def iter_messages(
        _entity: Any,
        *,
        limit: int,
        min_id: int = 0,
        max_id: int = 0,
        reverse: bool = False,
    ) -> _AsyncIter:
        selected = [
            i
            for i in stored
            if i > min_id and (max_id == 0 or i < max_id)
        ]
        if not reverse:
            selected.reverse()
        return _AsyncIter(_history(*selected[:limit]))

    client = _fake_client(entity=entity, history=[])
    client.iter_messages = MagicMock(side_effect=iter_messages)
    return client


def _output_ids(result: Any) -> list[int]:
    assert result.exit_code == 0, result.output
    return [m["id"] for m in json.loads(result.stdout)]


def _usage_error(result: Any) -> str:
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stderr)
    assert payload["type"] == "UsageError"
    return payload["error"]


def test_messages_after_id_reads_forward_from_that_id() -> None:
    entity = _entity(1)
    client = _fake_client(entity=entity, history=_history(101, 102, 105))
    result = _invoke(client, "1", "--after-id", "100")
    # Telethon already yields oldest-first in reverse mode, so the
    # output must keep the iterator's order.
    assert _output_ids(result) == [101, 102, 105]
    args, kwargs = client.iter_messages.call_args
    assert args == (entity,)
    assert kwargs["min_id"] == 100
    assert kwargs["reverse"] is True
    assert "max_id" not in kwargs


def test_messages_id_range_emits_links() -> None:
    entity = SimpleNamespace(
        id=1234567890, title="Dev", megagroup=True, username="dev_chat"
    )
    client = _fake_client(entity=entity, history=_history(101, 102))
    result = _invoke(client, "@dev_chat", "--after-id", "100")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert [m["link"] for m in data] == [
        "https://t.me/dev_chat/101",
        "https://t.me/dev_chat/102",
    ]


def test_messages_through_id_sets_exclusive_max_id() -> None:
    client = _fake_client(entity=_entity(1), history=[])
    result = _invoke(
        client, "1", "--after-id", "100", "--through-id", "200"
    )
    assert result.exit_code == 0, result.output
    _, kwargs = client.iter_messages.call_args
    assert kwargs["min_id"] == 100
    assert kwargs["max_id"] == 201
    assert kwargs["reverse"] is True


def test_messages_after_id_passes_limit_through() -> None:
    client = _fake_client(entity=_entity(1), history=[])
    result = _invoke(client, "1", "--after-id", "100", "--limit", "7")
    assert result.exit_code == 0, result.output
    _, kwargs = client.iter_messages.call_args
    assert kwargs["limit"] == 7


def test_messages_after_id_default_limit_is_100() -> None:
    client = _fake_client(entity=_entity(1), history=[])
    result = _invoke(client, "1", "--after-id", "100")
    assert result.exit_code == 0, result.output
    _, kwargs = client.iter_messages.call_args
    assert kwargs["limit"] == 100


def test_messages_id_range_excludes_after_id_and_includes_through_id() -> (
    None
):
    client = _client_filtering_by_id(_entity(1), [100, 101, 200, 201])
    result = _invoke(
        client, "1", "--after-id", "100", "--through-id", "200"
    )
    assert _output_ids(result) == [101, 200]


def test_messages_id_range_without_through_id_reads_to_the_newest() -> None:
    client = _client_filtering_by_id(_entity(1), [100, 101, 200, 201])
    result = _invoke(client, "1", "--after-id", "100")
    assert _output_ids(result) == [101, 200, 201]


def test_messages_id_range_tolerates_gaps_in_ids() -> None:
    client = _client_filtering_by_id(_entity(1), [3, 7, 20, 21, 50])
    result = _invoke(client, "1", "--after-id", "3", "--through-id", "21")
    assert _output_ids(result) == [7, 20, 21]


def test_messages_id_range_returns_the_oldest_limit_messages() -> None:
    client = _client_filtering_by_id(_entity(1), [11, 12, 13, 14, 15])
    result = _invoke(
        client,
        "1",
        "--after-id",
        "10",
        "--through-id",
        "15",
        "--limit",
        "3",
    )
    assert _output_ids(result) == [11, 12, 13]


def test_messages_id_range_holding_exactly_limit_messages() -> None:
    client = _client_filtering_by_id(_entity(1), [10, 11, 12, 13, 14])
    result = _invoke(
        client,
        "1",
        "--after-id",
        "10",
        "--through-id",
        "13",
        "--limit",
        "3",
    )
    assert _output_ids(result) == [11, 12, 13]


def test_messages_after_id_zero_reads_from_the_first_message() -> None:
    client = _fake_client(entity=_entity(1), history=_history(1, 2))
    result = _invoke(client, "1", "--after-id", "0")
    assert _output_ids(result) == [1, 2]
    _, kwargs = client.iter_messages.call_args
    assert kwargs["min_id"] == 0
    assert kwargs["reverse"] is True


def test_messages_after_id_with_since_is_a_usage_error() -> None:
    client = _fake_client(entity=_entity(1), history=[])
    result = _invoke(client, "1", "--after-id", "100", "--since", "24h")
    error = _usage_error(result)
    assert "--after-id" in error
    assert "--since" in error
    client.connect.assert_not_called()


def test_messages_through_id_without_after_id_is_a_usage_error() -> None:
    client = _fake_client(entity=_entity(1), history=[])
    result = _invoke(client, "1", "--through-id", "200")
    error = _usage_error(result)
    assert "--through-id" in error
    assert "--after-id" in error
    client.connect.assert_not_called()


@pytest.mark.parametrize("bad_id", ["-5", "abc", "2147483648"])
def test_messages_after_id_rejects_an_invalid_id(bad_id: str) -> None:
    client = _fake_client(entity=_entity(1), history=[])
    result = _invoke(client, "1", "--after-id", bad_id)
    error = _usage_error(result)
    assert "--after-id" in error
    assert bad_id in error
    client.connect.assert_not_called()


@pytest.mark.parametrize("bad_id", ["-5", "abc", "2147483648"])
def test_messages_through_id_rejects_an_invalid_id(bad_id: str) -> None:
    client = _fake_client(entity=_entity(1), history=[])
    result = _invoke(
        client, "1", "--after-id", "1", "--through-id", bad_id
    )
    error = _usage_error(result)
    assert "--through-id" in error
    assert bad_id in error
    client.connect.assert_not_called()


@pytest.mark.parametrize("through_id", ["100", "99", "0"])
def test_messages_through_id_at_or_below_after_id_is_empty(
    through_id: str,
) -> None:
    client = _fake_client(entity=_entity(1), history=_history(101))
    result = _invoke(
        client, "1", "--after-id", "100", "--through-id", through_id
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []
    client.iter_messages.assert_not_called()
    client.is_user_authorized.assert_awaited_once()
    client.get_entity.assert_awaited_once_with(1)
    client.disconnect.assert_awaited_once()


def test_messages_empty_id_range_still_reports_unknown_group() -> None:
    client = _fake_client(entity=_entity(1), history=[])
    client.get_entity = AsyncMock(
        side_effect=ValueError("Cannot find any entity")
    )
    result = _invoke(
        client, "1", "--after-id", "100", "--through-id", "100"
    )
    assert result.exit_code == 1
    payload = json.loads(result.stderr)
    assert payload["type"] == "GroupNotFoundError"
    client.iter_messages.assert_not_called()


def test_messages_empty_id_range_still_requires_a_session() -> None:
    client = _fake_client(entity=_entity(1), history=[], authorized=False)
    result = _invoke(
        client, "1", "--after-id", "100", "--through-id", "100"
    )
    assert result.exit_code == 1
    assert json.loads(result.stderr)["type"] == "AuthError"
    client.iter_messages.assert_not_called()


@pytest.mark.parametrize(
    "bounds",
    [
        ["--after-id", "2147483647"],
        ["--after-id", "2147483647", "--through-id", "2147483647"],
    ],
)
def test_messages_after_the_largest_id_is_empty(bounds: list[str]) -> None:
    client = _fake_client(entity=_entity(1), history=_history(5))
    result = _invoke(client, "1", *bounds)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []
    client.iter_messages.assert_not_called()
    client.get_entity.assert_awaited_once_with(1)


def test_messages_through_id_accepts_the_largest_id() -> None:
    client = _fake_client(entity=_entity(1), history=[])
    result = _invoke(
        client, "1", "--after-id", "100", "--through-id", "2147483647"
    )
    assert result.exit_code == 0, result.output
    _, kwargs = client.iter_messages.call_args
    assert kwargs["min_id"] == 100
    assert kwargs["max_id"] == 2147483648


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


def test_to_message_handles_non_integer_sender_id() -> None:
    """Telethon can set ``sender_id`` to a ``Peer*`` object for
    anonymous-admin or channel-signature messages; ``int(peer)`` raises
    ``TypeError``. The helper must degrade to ``None`` rather than
    propagating a traceback."""

    class _Peer:
        def __int__(self) -> int:
            raise TypeError("Peer is not int-castable")

    raw = SimpleNamespace(
        id=1,
        message="anon",
        text=None,
        date=datetime(2026, 4, 17, tzinfo=timezone.utc),
        sender=None,
        sender_id=_Peer(),
        reply_to=None,
    )
    msg = message_mod.to_message(raw, group_id=42)
    assert msg.sender_id is None
    assert msg.to_dict()["sender_id"] is None


def test_to_message_handles_missing_date_without_crashing() -> None:
    """Some Telethon payloads (service messages) can carry ``date=None``.
    ``Message.to_dict()`` must not crash when serialising the batch."""
    raw = SimpleNamespace(
        id=1,
        message="service",
        text=None,
        date=None,
        sender=None,
        sender_id=None,
        reply_to=None,
    )
    msg = message_mod.to_message(raw, group_id=42)
    # ``to_dict`` must not crash.
    serialised = msg.to_dict()
    assert serialised["id"] == 1
    assert isinstance(serialised["date"], str)
