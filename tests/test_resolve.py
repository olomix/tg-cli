"""Tests for the ``commands._resolve`` group resolver."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon.errors import (
    ChannelInvalidError,
    ChannelPrivateError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)

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
        # Accept either a single value (back-compat, wrapped into a
        # one-shot side_effect list) or a sequence of values for tests
        # that need multiple sequential ``get_entity`` calls (e.g.
        # follow-through from a migrated basic chat).
        if isinstance(get_entity, (list, tuple)):
            side_effect = list(get_entity)
        else:
            side_effect = [get_entity]
        client.get_entity = AsyncMock(side_effect=side_effect)
    return client


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_resolve_numeric_id_passes_int_to_get_entity() -> None:
    sentinel = SimpleNamespace(title="My Group")
    client = _client(get_entity=sentinel)
    result = _run(_resolve.resolve(client, "-1001234567890"))
    assert result is sentinel
    client.get_entity.assert_awaited_once_with(-1001234567890)
    client.iter_dialogs.assert_not_called()


def test_resolve_positive_numeric_id() -> None:
    sentinel = SimpleNamespace(title="My Group")
    client = _client(get_entity=sentinel)
    _run(_resolve.resolve(client, "12345"))
    client.get_entity.assert_awaited_once_with(12345)


def test_resolve_at_username_passes_string_to_get_entity() -> None:
    sentinel = SimpleNamespace(title="My Dev Group")
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


def test_resolve_title_substring_skips_dialogs_without_entity_title() -> None:
    # Telethon ``User`` dialogs (DMs, bots) have no ``title`` but do
    # populate ``dialog.name`` with the contact's name — must NOT match
    # the title-substring path or the skill's group-only contract leaks.
    dm = SimpleNamespace(
        entity=SimpleNamespace(id=10, first_name="Alice"),
        id=10,
        name="Alice",
    )
    client = _client([dm])
    with pytest.raises(_resolve.GroupNotFoundError):
        _run(_resolve.resolve(client, "alice"))


def test_resolve_numeric_id_rejects_user_entity() -> None:
    # A positive numeric id can resolve to a ``User`` (DM, bot). Passing
    # one to ``tg messages`` would otherwise return DM history under the
    # guise of a group fetch.
    user_entity = SimpleNamespace(id=12345, first_name="Alice")
    client = _client(get_entity=user_entity)
    with pytest.raises(_resolve.GroupNotFoundError) as excinfo:
        _run(_resolve.resolve(client, "12345"))
    assert "12345" in str(excinfo.value)


def test_resolve_at_username_rejects_user_entity() -> None:
    user_entity = SimpleNamespace(id=42, first_name="Alice", username="alice")
    client = _client(get_entity=user_entity)
    with pytest.raises(_resolve.GroupNotFoundError) as excinfo:
        _run(_resolve.resolve(client, "@alice"))
    assert "@alice" in str(excinfo.value)


def test_resolve_numeric_id_accepts_chat_entity() -> None:
    chat_entity = SimpleNamespace(id=-1001234567890, title="My Group")
    client = _client(get_entity=chat_entity)
    result = _run(_resolve.resolve(client, "-1001234567890"))
    assert result is chat_entity


def test_resolve_numeric_id_value_error_becomes_group_not_found() -> None:
    client = MagicMock()
    client.get_entity = AsyncMock(
        side_effect=ValueError("Cannot find any entity")
    )
    with pytest.raises(_resolve.GroupNotFoundError) as excinfo:
        _run(_resolve.resolve(client, "-100999"))
    assert "-100999" in str(excinfo.value)


def test_resolve_username_not_occupied_becomes_group_not_found() -> None:
    # Telethon RPC subclasses take a ``request`` argument; pass a dummy
    # so the real ``__init__`` runs and we catch any future signature drift.
    exc = UsernameNotOccupiedError(request=SimpleNamespace())
    client = MagicMock()
    client.get_entity = AsyncMock(side_effect=exc)
    with pytest.raises(_resolve.GroupNotFoundError) as excinfo:
        _run(_resolve.resolve(client, "@nonexistent"))
    assert "@nonexistent" in str(excinfo.value)


def test_resolve_username_invalid_becomes_group_not_found() -> None:
    exc = UsernameInvalidError(request=SimpleNamespace())
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


def _migrated_chat(title: str = "Old Group") -> SimpleNamespace:
    """Basic ``Chat`` double whose ``migrated_to`` points at a channel.

    ``id=584241293`` is synthetic — chosen to resemble a real basic-chat
    id (9-digit bare positive int, marked form ``-584241293``) without
    colliding with other fixture ids in the suite.
    """
    pointer = SimpleNamespace(channel_id=999, access_hash=42)
    return SimpleNamespace(id=584241293, title=title, migrated_to=pointer)


def test_resolve_numeric_id_follows_migration_to_channel() -> None:
    migrated = _migrated_chat()
    channel = SimpleNamespace(id=999, title="New Supergroup", megagroup=True)
    client = _client(get_entity=[migrated, channel])
    result = _run(_resolve.resolve(client, "-584241293"))
    assert result is channel
    assert client.get_entity.await_count == 2
    # Second call uses the ``migrated_to`` pointer verbatim.
    second_call = client.get_entity.await_args_list[1]
    assert second_call.args == (migrated.migrated_to,)


def test_resolve_migration_follow_value_error_raises_group_not_found() -> None:
    migrated = _migrated_chat()
    client = _client(get_entity=[migrated, ValueError("stale hash")])
    with pytest.raises(_resolve.GroupNotFoundError) as excinfo:
        _run(_resolve.resolve(client, "-584241293"))
    assert "migrated" in str(excinfo.value)


def test_resolve_migration_follow_channel_invalid_raises_not_found() -> None:
    migrated = _migrated_chat()
    exc = ChannelInvalidError(request=SimpleNamespace())
    client = _client(get_entity=[migrated, exc])
    with pytest.raises(_resolve.GroupNotFoundError) as excinfo:
        _run(_resolve.resolve(client, "-584241293"))
    assert "migrated" in str(excinfo.value)


def test_resolve_migration_follow_channel_private_raises_not_found() -> None:
    migrated = _migrated_chat()
    exc = ChannelPrivateError(request=SimpleNamespace())
    client = _client(get_entity=[migrated, exc])
    with pytest.raises(_resolve.GroupNotFoundError) as excinfo:
        _run(_resolve.resolve(client, "-584241293"))
    assert "migrated" in str(excinfo.value)


def test_resolve_migration_follow_unrelated_exception_propagates() -> None:
    # ``_maybe_follow_migration`` catches only the documented staleness /
    # access-check errors. An unrelated exception (connection loss, bug,
    # etc.) must bubble up unchanged — wrapping it in
    # ``GroupNotFoundError`` would mask real failures.
    migrated = _migrated_chat()
    unrelated = RuntimeError("transport died")
    client = _client(get_entity=[migrated, unrelated])
    with pytest.raises(RuntimeError) as excinfo:
        _run(_resolve.resolve(client, "-584241293"))
    assert str(excinfo.value) == "transport died"


def test_resolve_migration_follow_to_non_group_entity_raises_not_found() -> (
    None
):
    # If Telegram's migrated_to pointer ever resolves to something
    # title-less (a corrupt DB entry, a user peer), the numeric/@handle
    # path must surface ``GroupNotFoundError`` rather than hand a DM/bot
    # entity to downstream commands.
    migrated = _migrated_chat()
    not_a_group = SimpleNamespace(id=999, first_name="Alice")
    client = _client(get_entity=[migrated, not_a_group])
    with pytest.raises(_resolve.GroupNotFoundError) as excinfo:
        _run(_resolve.resolve(client, "-584241293"))
    assert "non-group entity" in str(excinfo.value)


def test_resolve_non_migrated_numeric_id_does_not_double_lookup() -> None:
    live_chat = SimpleNamespace(
        id=1234, title="Live Basic Chat", migrated_to=None
    )
    client = _client(get_entity=live_chat)
    result = _run(_resolve.resolve(client, "-1234"))
    assert result is live_chat
    client.get_entity.assert_awaited_once_with(-1234)


def test_resolve_username_live_channel_no_migration_follow() -> None:
    channel = SimpleNamespace(
        id=1001, title="Live Channel", megagroup=True, migrated_to=None
    )
    client = _client(get_entity=channel)
    result = _run(_resolve.resolve(client, "@livechannel"))
    assert result is channel
    client.get_entity.assert_awaited_once_with("@livechannel")


def test_resolve_title_substring_follows_migration_to_channel() -> None:
    migrated = _migrated_chat(title="Old Group Name")
    channel = SimpleNamespace(id=999, title="New Supergroup", megagroup=True)
    dialog = SimpleNamespace(
        entity=migrated, id=migrated.id, name=migrated.title
    )
    client = _client([dialog], get_entity=channel)
    result = _run(_resolve.resolve(client, "old group name"))
    assert result is channel
    # Only the follow-through lookup fires; no initial ``get_entity`` for the
    # title path (that branch walks dialogs instead).
    client.get_entity.assert_awaited_once_with(migrated.migrated_to)


def test_resolve_title_substring_migration_follow_channel_invalid() -> None:
    migrated = _migrated_chat(title="Old Group Name")
    dialog = SimpleNamespace(
        entity=migrated, id=migrated.id, name=migrated.title
    )
    exc = ChannelInvalidError(request=SimpleNamespace())
    client = _client([dialog], get_entity=exc)
    with pytest.raises(_resolve.GroupNotFoundError) as excinfo:
        _run(_resolve.resolve(client, "old group name"))
    assert "migrated" in str(excinfo.value)
    # Error message names the user's query, not some internal id.
    assert "old group name" in str(excinfo.value)


def test_resolve_title_substring_migration_follow_to_non_group_raises() -> None:
    # Title path must mirror the numeric/@handle guard: if the migrated
    # target is not a group-shaped entity, surface ``GroupNotFoundError``
    # rather than return a user/bot to downstream commands.
    migrated = _migrated_chat(title="Old Group Name")
    dialog = SimpleNamespace(
        entity=migrated, id=migrated.id, name=migrated.title
    )
    not_a_group = SimpleNamespace(id=999, first_name="Alice")
    client = _client([dialog], get_entity=not_a_group)
    with pytest.raises(_resolve.GroupNotFoundError) as excinfo:
        _run(_resolve.resolve(client, "old group name"))
    assert "non-group entity" in str(excinfo.value)


def test_resolve_title_substring_dedupes_zombie_and_migration_target() -> None:
    # If the user's substring matches both the migrated zombie *and* its
    # replacement supergroup (e.g. both still carry the shared name in
    # the dialog list), ``_resolve_by_title`` must follow the migration
    # and dedupe by post-migration id — surfacing the single legitimate
    # target instead of raising ``AmbiguousGroupError``.
    channel = SimpleNamespace(
        id=999, title="Team Name", megagroup=True, migrated_to=None
    )
    migrated = SimpleNamespace(
        id=584241293,
        title="Team Name",
        migrated_to=SimpleNamespace(channel_id=999, access_hash=42),
    )
    dialogs = [
        SimpleNamespace(entity=migrated, id=migrated.id, name=migrated.title),
        SimpleNamespace(entity=channel, id=channel.id, name=channel.title),
    ]
    # Only the migrated entry triggers a ``get_entity`` call (for the
    # follow-through); the live channel is returned as-is.
    client = _client(dialogs, get_entity=channel)
    result = _run(_resolve.resolve(client, "team name"))
    assert result is channel
    client.get_entity.assert_awaited_once_with(migrated.migrated_to)


def test_resolve_title_substring_still_ambiguous_across_distinct_groups() -> (
    None
):
    # Regression guard for the dedupe logic: two genuinely distinct
    # groups must still raise ``AmbiguousGroupError``.
    a = SimpleNamespace(id=1, title="Dev Frontend", migrated_to=None)
    b = SimpleNamespace(id=2, title="Dev Backend", migrated_to=None)
    dialogs = [
        SimpleNamespace(entity=a, id=a.id, name=a.title),
        SimpleNamespace(entity=b, id=b.id, name=b.title),
    ]
    client = _client(dialogs)
    with pytest.raises(_resolve.AmbiguousGroupError) as excinfo:
        _run(_resolve.resolve(client, "dev"))
    assert excinfo.value.matches == ["Dev Frontend", "Dev Backend"]


def test_resolve_title_substring_bare_id_collision_across_peer_types() -> None:
    # Telegram only guarantees id uniqueness within a peer type: a small
    # ``Chat(id=N)`` and a ``Channel(id=N)`` are different peers whose
    # marked ids differ (``-N`` vs ``-1e12-N``). The dedupe key must be
    # the marked peer id, otherwise one match is silently dropped and
    # what should raise ``AmbiguousGroupError`` resolves to the wrong
    # entity.
    chat = SimpleNamespace(id=100, title="Shared Name", migrated_to=None)
    channel = SimpleNamespace(
        id=100, title="Shared Name Channel", megagroup=True, migrated_to=None
    )
    dialogs = [
        SimpleNamespace(entity=chat, id=chat.id, name=chat.title),
        SimpleNamespace(entity=channel, id=channel.id, name=channel.title),
    ]
    client = _client(dialogs)
    with pytest.raises(_resolve.AmbiguousGroupError) as excinfo:
        _run(_resolve.resolve(client, "shared"))
    assert excinfo.value.matches == ["Shared Name", "Shared Name Channel"]


def test_resolve_title_substring_live_channel_no_get_entity_call() -> None:
    # Regression guard: the existing title-substring path must not gain a
    # redundant ``get_entity`` lookup for non-migrated matches.
    live_channel = SimpleNamespace(
        id=1001, title="Live Channel", megagroup=True, migrated_to=None
    )
    dialog = SimpleNamespace(
        entity=live_channel, id=live_channel.id, name=live_channel.title
    )
    client = _client([dialog])
    result = _run(_resolve.resolve(client, "live channel"))
    assert result is live_channel
    client.get_entity.assert_not_awaited()
