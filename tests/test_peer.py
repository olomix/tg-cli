"""Tests for the marked-peer-id helper."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from telethon.tl import types
from telethon.utils import get_peer_id

from tg_cli.commands._peer import is_migrated_chat, marked_peer_id


def test_marked_peer_id_for_small_chat() -> None:
    # ``Chat`` entities lack ``megagroup``/``broadcast``; marked = ``-bare``.
    chat = SimpleNamespace(id=42, title="Small")
    assert marked_peer_id(chat) == -42


def test_marked_peer_id_for_supergroup() -> None:
    entity = SimpleNamespace(
        id=1234567890, title="Mega", megagroup=True, broadcast=False
    )
    assert marked_peer_id(entity) == -1001234567890


def test_marked_peer_id_for_broadcast_channel() -> None:
    entity = SimpleNamespace(
        id=9876543210, title="News", megagroup=False, broadcast=True
    )
    assert marked_peer_id(entity) == -1009876543210


def test_marked_peer_id_for_gigagroup_mock() -> None:
    # Gigagroups are supergroups upgraded to broadcast-like behavior.
    # They still live under the ``Channel`` CRC, so the marked id uses
    # the ``-1e12`` offset regardless of the megagroup/broadcast flags.
    entity = SimpleNamespace(
        id=5555555555,
        title="Giga",
        megagroup=False,
        broadcast=False,
        gigagroup=True,
    )
    assert marked_peer_id(entity) == -1005555555555


@pytest.mark.parametrize(
    "flags",
    [
        {"megagroup": True},
        {"broadcast": True},
        {"gigagroup": True},
        {},  # Channel with no flags set — still a Channel.
    ],
)
def test_marked_peer_id_matches_telethon_for_channel_variants(
    flags: dict[str, bool],
) -> None:
    # Our mapping must agree with Telethon's CRC-based ``get_peer_id``
    # for every ``Channel`` variant, so the JSON contract round-trips
    # through ``client.get_entity``.
    real = types.Channel(
        id=1234567890,
        title="X",
        photo=None,
        date=None,
        access_hash=None,
        **flags,
    )
    assert marked_peer_id(real) == get_peer_id(real)


@pytest.mark.parametrize(
    "flags",
    [
        {},  # No flags — still a channel-space peer.
        {"megagroup": True},
        {"broadcast": True},
    ],
)
def test_marked_peer_id_matches_telethon_for_channel_forbidden(
    flags: dict[str, bool],
) -> None:
    # ``ChannelForbidden`` is a sibling of ``Channel`` (not a subclass)
    # but lives in the same ``-1e12`` marked-id space — kicked/banned
    # dialogs surface this type and must round-trip correctly.
    forbidden = types.ChannelForbidden(
        id=126,
        access_hash=0,
        title="Forbidden",
        **flags,
    )
    assert marked_peer_id(forbidden) == get_peer_id(forbidden)


def test_marked_peer_id_for_chat_forbidden() -> None:
    # ``ChatForbidden`` lives in the small-chat id space (``-id``),
    # like a regular ``Chat``.
    forbidden = types.ChatForbidden(id=200, title="Banned chat")
    assert marked_peer_id(forbidden) == get_peer_id(forbidden)


def test_marked_peer_id_rejects_missing_id() -> None:
    with pytest.raises(AttributeError):
        marked_peer_id(SimpleNamespace(title="no id"))


def test_is_migrated_chat_true_when_migrated_to_is_set() -> None:
    # Telethon fills ``migrated_to`` with an ``InputChannel`` on a chat
    # that has been upgraded to a supergroup; any truthy value suffices
    # for the predicate.
    pointer = SimpleNamespace(channel_id=1234567890, access_hash=42)
    chat = SimpleNamespace(id=100, title="Old", migrated_to=pointer)
    assert is_migrated_chat(chat) is True


def test_is_migrated_chat_false_when_migrated_to_is_none() -> None:
    chat = SimpleNamespace(id=100, title="Live", migrated_to=None)
    assert is_migrated_chat(chat) is False


def test_is_migrated_chat_false_when_attribute_missing() -> None:
    # ``Channel`` / ``ChannelForbidden`` and our test doubles typically
    # don't declare ``migrated_to`` at all — must read as "not migrated".
    channel = SimpleNamespace(id=1, title="Live", megagroup=True)
    assert is_migrated_chat(channel) is False
    forbidden = types.ChatForbidden(id=200, title="Banned chat")
    assert is_migrated_chat(forbidden) is False
