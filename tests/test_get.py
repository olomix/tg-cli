"""Tests for the ``tg get`` command."""

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

_DAY = datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc)


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
    id: int, *, sender: SimpleNamespace | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        message=f"m{id}",
        text=f"m{id}",
        date=_DAY,
        sender=sender,
        sender_id=42 if sender is not None else None,
        reply_to=None,
    )


def _entity(id: int, title: str = "Group") -> SimpleNamespace:
    # Bare positive id + channel flags match real Telethon shape; the
    # command converts this to a marked peer id for ``group_id`` output.
    return SimpleNamespace(id=id, title=title, megagroup=True, broadcast=False)


def _fake_client(
    *,
    entity: SimpleNamespace,
    stored: Iterable[SimpleNamespace] = (),
    authorized: bool = True,
) -> MagicMock:
    """Client whose ``get_messages`` gives the usual answer for a list
    of ids: request order, ``None`` for an id that does not exist."""
    by_id = {m.id: m for m in stored}

    async def get_messages(_entity: Any, *, ids: list[int]) -> list[Any]:
        return [by_id.get(i) for i in ids]

    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=authorized)
    client.get_entity = AsyncMock(return_value=entity)
    client.iter_dialogs = MagicMock(return_value=_AsyncIter([]))
    client.get_messages = AsyncMock(side_effect=get_messages)
    return client


def _invoke(client: MagicMock, *args: str) -> Any:
    with patch("tg_cli.commands.get.make_client", return_value=client):
        return CliRunner().invoke(cli.main, ["get", *args])


def _output_ids(result: Any) -> list[int]:
    assert result.exit_code == 0, result.output
    return [m["id"] for m in json.loads(result.stdout)]


def _usage_error(result: Any) -> str:
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stderr)
    assert payload["type"] == "UsageError"
    return payload["error"]


def test_get_returns_messages_in_the_order_requested() -> None:
    entity = _entity(1234567890, "Dev")
    client = _fake_client(
        entity=entity, stored=[_msg(10), _msg(11), _msg(12)]
    )
    result = _invoke(client, "@dev", "12", "10", "11")
    assert _output_ids(result) == [12, 10, 11]
    client.get_entity.assert_awaited_once_with("@dev")
    client.get_messages.assert_awaited_once_with(entity, ids=[12, 10, 11])


def test_get_outputs_contract_shape_with_link() -> None:
    # Bare channel id ``1234567890`` → marked peer id ``-1001234567890``.
    entity = _entity(1234567890, "Dev")
    sender = SimpleNamespace(
        first_name="Alice", last_name="Doe", username="alice"
    )
    client = _fake_client(
        entity=entity, stored=[_msg(10, sender=sender), _msg(11)]
    )
    result = _invoke(client, "@dev", "10", "11")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data[0] == {
        "id": 10,
        "date": "2026-04-17T10:00:00+00:00",
        "sender_id": 42,
        "sender_name": "Alice Doe",
        "text": "m10",
        "reply_to_id": None,
        "group_id": -1001234567890,
        "sender_username": "alice",
        "topic_id": None,
        "media_kind": None,
        "grouped_id": None,
        "urls": [],
        "forward": None,
        "link": "https://t.me/c/1234567890/10",
    }
    assert data[1]["link"] == "https://t.me/c/1234567890/11"


def test_get_on_public_group_emits_username_links() -> None:
    entity = SimpleNamespace(
        id=1234567890, title="Dev", megagroup=True, username="dev_chat"
    )
    client = _fake_client(entity=entity, stored=[_msg(7)])
    result = _invoke(client, "@dev_chat", "7")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["link"] == "https://t.me/dev_chat/7"


def test_get_omits_ids_that_do_not_exist() -> None:
    client = _fake_client(entity=_entity(1), stored=[_msg(10), _msg(12)])
    result = _invoke(client, "1", "10", "11", "12")
    assert _output_ids(result) == [10, 12]


def test_get_all_ids_missing_prints_empty_array() -> None:
    client = _fake_client(entity=_entity(1), stored=[])
    result = _invoke(client, "1", "10", "11")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []
    client.get_messages.assert_awaited_once()


def test_get_requests_a_single_id_as_a_list() -> None:
    """A bare int makes Telethon return one message instead of a list."""
    entity = _entity(1)
    client = _fake_client(entity=entity, stored=[_msg(5)])
    result = _invoke(client, "1", "5")
    assert _output_ids(result) == [5]
    client.get_messages.assert_awaited_once_with(entity, ids=[5])


def test_get_accepts_the_largest_message_id() -> None:
    entity = _entity(1)
    client = _fake_client(entity=entity, stored=[])
    result = _invoke(client, "1", "2147483647")
    assert result.exit_code == 0, result.output
    client.get_messages.assert_awaited_once_with(entity, ids=[2147483647])


def test_get_pretty_indents_json() -> None:
    client = _fake_client(entity=_entity(1), stored=[_msg(5)])
    result = _invoke(client, "1", "5", "--pretty")
    assert result.exit_code == 0, result.output
    assert "\n  " in result.stdout
    assert json.loads(result.stdout)[0]["id"] == 5


def test_get_accepts_negative_group_id_after_separator() -> None:
    entity = _entity(1234567890)
    client = _fake_client(entity=entity, stored=[_msg(5), _msg(6)])
    result = _invoke(client, "--", "-1001234567890", "5", "6")
    assert _output_ids(result) == [5, 6]
    client.get_entity.assert_awaited_once_with(-1001234567890)
    client.get_messages.assert_awaited_once_with(entity, ids=[5, 6])


def test_get_disconnects_on_success() -> None:
    client = _fake_client(entity=_entity(1), stored=[_msg(5)])
    result = _invoke(client, "1", "5")
    assert result.exit_code == 0, result.output
    client.connect.assert_awaited_once()
    client.disconnect.assert_awaited_once()


def test_get_without_ids_is_a_usage_error() -> None:
    client = _fake_client(entity=_entity(1))
    result = _invoke(client, "1")
    error = _usage_error(result)
    assert "MESSAGE_IDS" in error
    client.connect.assert_not_called()


@pytest.mark.parametrize("bad_id", ["0", "abc", "2147483648"])
def test_get_rejects_an_invalid_id(bad_id: str) -> None:
    client = _fake_client(entity=_entity(1), stored=[_msg(5)])
    result = _invoke(client, "1", "5", bad_id)
    error = _usage_error(result)
    assert "MESSAGE_IDS" in error
    assert bad_id in error
    client.connect.assert_not_called()


@pytest.mark.parametrize("argv", [["1", "-5"], ["--", "1", "-5"]])
def test_get_rejects_a_negative_id(argv: list[str]) -> None:
    client = _fake_client(entity=_entity(1), stored=[_msg(5)])
    result = _invoke(client, *argv)
    assert "-5" in _usage_error(result)
    client.connect.assert_not_called()


def test_get_errors_when_not_authorized() -> None:
    client = _fake_client(
        entity=_entity(1), stored=[_msg(5)], authorized=False
    )
    result = _invoke(client, "1", "5")
    assert result.exit_code == 1
    payload = json.loads(result.stderr)
    assert payload["type"] == "AuthError"
    assert "tg login" in payload["error"]
    client.get_messages.assert_not_called()
    client.disconnect.assert_awaited_once()


def test_get_unknown_group_reports_group_not_found() -> None:
    client = _fake_client(entity=_entity(1), stored=[_msg(5)])
    client.get_entity = AsyncMock(
        side_effect=ValueError("Cannot find any entity")
    )
    result = _invoke(client, "1", "5")
    assert result.exit_code == 1
    payload = json.loads(result.stderr)
    assert payload["type"] == "GroupNotFoundError"
    client.get_messages.assert_not_called()
    client.disconnect.assert_awaited_once()


# --- pairing answers with ids --------------------------------------------


def _answer(client: MagicMock, *messages: Any) -> None:
    """Make ``get_messages`` return exactly ``messages``, whatever ids
    it is asked for."""
    client.get_messages = AsyncMock(return_value=list(messages))


def test_get_skips_an_id_telegram_left_out_of_its_answer() -> None:
    client = _fake_client(entity=_entity(1))
    _answer(client, _msg(5), _msg(7))
    result = _invoke(client, "1", "5", "6", "7")
    assert _output_ids(result) == [5, 7]


def test_get_keeps_the_request_order_when_the_answer_has_another() -> None:
    client = _fake_client(entity=_entity(1))
    _answer(client, _msg(5), None, _msg(7))
    result = _invoke(client, "1", "7", "6", "5")
    assert _output_ids(result) == [7, 5]


@pytest.mark.parametrize("copies", [1, 2])
def test_get_returns_an_id_requested_twice_twice(copies: int) -> None:
    client = _fake_client(entity=_entity(1))
    _answer(client, *[_msg(5)] * copies, _msg(6))
    result = _invoke(client, "1", "5", "6", "5")
    assert _output_ids(result) == [5, 6, 5]


def test_get_prints_an_empty_array_for_an_empty_answer() -> None:
    client = _fake_client(entity=_entity(1))
    _answer(client)
    result = _invoke(client, "1", "5", "6")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []
