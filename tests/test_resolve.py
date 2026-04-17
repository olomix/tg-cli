"""Tests for the ``commands._resolve`` group resolver."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon.errors import UsernameInvalidError, UsernameNotOccupiedError

from tg_cli.commands import _resolve


class _AsyncIter:
    def __init__(self, items: Iterable[Any]) -> None:
        self._items = list(items)

    def __aiter__(self) -> _AsyncIter:
        return self

    async def __anext__(self) -> Any:
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


def _dialog(*, id: int, title: str) -> SimpleNamespace:
    entity = SimpleNamespace(id=id, title=title)
    return SimpleNamespace(entity=entity, id=id, name=title)


def _client(
    dialogs: Iterable[Any] | None = None,
    *,
    get_entity: Any = None,
) -> MagicMock:
    client = MagicMock()
    client.iter_dialogs = MagicMock(
        return_value=_AsyncIter(list(dialogs or []))
    )
    if get_entity is None:
        client.get_entity = AsyncMock(return_value=SimpleNamespace())
    else:
        client.get_entity = AsyncMock(side_effect=[get_entity])
    return client


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_resolve_numeric_id_passes_int_to_get_entity() -> None:
    sentinel = SimpleNamespace(name="entity")
    client = _client(get_entity=sentinel)
    result = _run(_resolve.resolve(client, "-1001234567890"))
    assert result is sentinel
    client.get_entity.assert_awaited_once_with(-1001234567890)
    client.iter_dialogs.assert_not_called()


def test_resolve_positive_numeric_id() -> None:
    sentinel = SimpleNamespace()
    client = _client(get_entity=sentinel)
    _run(_resolve.resolve(client, "12345"))
    client.get_entity.assert_awaited_once_with(12345)


def test_resolve_at_username_passes_string_to_get_entity() -> None:
    sentinel = SimpleNamespace()
    client = _client(get_entity=sentinel)
    _run(_resolve.resolve(client, "@mydevgroup"))
    client.get_entity.assert_awaited_once_with("@mydevgroup")
    client.iter_dialogs.assert_not_called()


def test_resolve_title_substring_case_insensitive_unique_match() -> None:
    target = _dialog(id=2, title="My Dev Group")
    dialogs = [_dialog(id=1, title="News"), target, _dialog(id=3, title="Misc")]
    client = _client(dialogs)
    result = _run(_resolve.resolve(client, "dev"))
    assert result is target.entity


def test_resolve_title_substring_matches_full_title_case_insensitive() -> None:
    target = _dialog(id=1, title="Frontend Team")
    client = _client([target])
    result = _run(_resolve.resolve(client, "FRONTEND TEAM"))
    assert result is target.entity


def test_resolve_title_substring_raises_when_no_match() -> None:
    client = _client([_dialog(id=1, title="News")])
    with pytest.raises(_resolve.GroupNotFoundError):
        _run(_resolve.resolve(client, "missing"))


def test_resolve_title_substring_raises_when_ambiguous() -> None:
    dialogs = [
        _dialog(id=1, title="Dev Frontend"),
        _dialog(id=2, title="Dev Backend"),
    ]
    client = _client(dialogs)
    with pytest.raises(_resolve.AmbiguousGroupError) as excinfo:
        _run(_resolve.resolve(client, "dev"))
    assert excinfo.value.matches == ["Dev Frontend", "Dev Backend"]
    assert excinfo.value.query == "dev"
    # Error message lists candidates so the user can disambiguate.
    assert "Dev Frontend" in str(excinfo.value)
    assert "Dev Backend" in str(excinfo.value)


def test_resolve_strips_whitespace() -> None:
    target = _dialog(id=1, title="My Group")
    client = _client([target])
    result = _run(_resolve.resolve(client, "  my group  "))
    assert result is target.entity


def test_resolve_rejects_empty_reference() -> None:
    client = _client()
    with pytest.raises(_resolve.GroupResolveError):
        _run(_resolve.resolve(client, ""))


def test_resolve_dialog_without_title_falls_back_to_name() -> None:
    entity = SimpleNamespace(id=10)  # no title attr
    dialog = SimpleNamespace(entity=entity, id=10, name="Fallback")
    client = _client([dialog])
    result = _run(_resolve.resolve(client, "fallback"))
    assert result is entity


def test_resolve_numeric_id_value_error_becomes_group_not_found() -> None:
    client = MagicMock()
    client.get_entity = AsyncMock(
        side_effect=ValueError("Cannot find any entity")
    )
    with pytest.raises(_resolve.GroupNotFoundError) as excinfo:
        _run(_resolve.resolve(client, "-100999"))
    assert "-100999" in str(excinfo.value)


def test_resolve_username_not_occupied_becomes_group_not_found() -> None:
    # Telethon RPC errors need positional ``request`` arg in some versions;
    # instantiate via ``__new__`` to avoid version-specific constructor drift.
    exc = UsernameNotOccupiedError.__new__(UsernameNotOccupiedError)
    Exception.__init__(exc, "USERNAME_NOT_OCCUPIED")
    client = MagicMock()
    client.get_entity = AsyncMock(side_effect=exc)
    with pytest.raises(_resolve.GroupNotFoundError) as excinfo:
        _run(_resolve.resolve(client, "@nonexistent"))
    assert "@nonexistent" in str(excinfo.value)


def test_resolve_username_invalid_becomes_group_not_found() -> None:
    exc = UsernameInvalidError.__new__(UsernameInvalidError)
    Exception.__init__(exc, "USERNAME_INVALID")
    client = MagicMock()
    client.get_entity = AsyncMock(side_effect=exc)
    with pytest.raises(_resolve.GroupNotFoundError):
        _run(_resolve.resolve(client, "@bad handle"))


def test_resolve_skips_dialogs_with_no_title_or_name() -> None:
    nameless = SimpleNamespace(
        entity=SimpleNamespace(id=1), id=1, name=None
    )
    target = _dialog(id=2, title="Hits")
    client = _client([nameless, target])
    result = _run(_resolve.resolve(client, "hits"))
    assert result is target.entity
