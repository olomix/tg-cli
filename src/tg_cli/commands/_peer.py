"""Marked-peer-id helper.

Telethon's ``Chat``/``Channel`` entities expose a *bare* positive ``id``,
but the JSON contract in ``README.md`` and ``skill/SKILL.md`` documents
*marked* peer ids (e.g. ``-1001234567890`` for channels / supergroups,
``-12345`` for small chats). Marked ids are required for two reasons:

1. Consumers need to round-trip the id back through
   ``client.get_entity(int)`` — a bare positive int is interpreted by
   Telethon as a ``User`` id and would resolve the wrong peer.
2. The sign/magnitude of the id disambiguates peer type without a
   separate field.

We ``isinstance``-check against both ``telethon.tl.types.Channel`` and
``telethon.tl.types.ChannelForbidden`` (a sibling class — *not* a
``Channel`` subclass — returned for channels you've been kicked from
or otherwise can't access; it lives in the same ``-1e12`` marked-id
space) and fall back to flag inspection so the helper still works
against the ``SimpleNamespace`` mocks the unit tests use.
"""

from __future__ import annotations

from typing import Any

from telethon.tl import types as _tl

_CHANNEL_ID_OFFSET = -1_000_000_000_000
_CHANNEL_TYPES: tuple[type, ...] = (_tl.Channel, _tl.ChannelForbidden)


def marked_peer_id(entity: Any) -> int:
    """Return Telethon's marked peer id for a chat/channel ``entity``.

    Any Telethon ``Channel`` or ``ChannelForbidden`` — broadcast,
    megagroup, gigagroup, or flag-less — maps to ``-1e12 - id``; plain
    ``Chat``/``ChatForbidden`` entities map to ``-id``. Matches
    ``telethon.utils.get_peer_id``.
    """
    bare = int(entity.id)
    if _is_channel(entity):
        return _CHANNEL_ID_OFFSET - bare
    return -bare


def message_link_base(entity: Any) -> str | None:
    """Return the ``t.me`` prefix shared by every message link of a group.

    ``https://t.me/<username>`` for a public group or channel,
    ``https://t.me/c/<bare id>`` for a private channel or supergroup,
    and ``None`` for a basic group, which has no message permalinks.
    """
    username = _public_username(entity)
    if username:
        return f"https://t.me/{username}"
    if _is_channel(entity):
        return f"https://t.me/c/{int(entity.id)}"
    return None


def _public_username(entity: Any) -> str | None:
    username = getattr(entity, "username", None)
    if username:
        return str(username)
    # A group with several usernames can leave ``username`` empty and
    # list them only here.
    for entry in getattr(entity, "usernames", None) or []:
        if getattr(entry, "active", False) and entry.username:
            return str(entry.username)
    return None


def _is_channel(entity: Any) -> bool:
    return isinstance(entity, _CHANNEL_TYPES) or _looks_like_channel(entity)


def _looks_like_channel(entity: Any) -> bool:
    """Flag-based fallback for non-Telethon test doubles."""
    return (
        getattr(entity, "megagroup", False)
        or getattr(entity, "broadcast", False)
        or getattr(entity, "gigagroup", False)
    )


def is_group_entity(entity: Any) -> bool:
    """Return ``True`` for ``Chat``/``Channel`` entities.

    Telethon ``User`` objects (DMs, bots) lack a ``title`` attribute; a
    truthy ``title`` is the shape-test shared by the dialog lister and
    the resolver so they stay in sync on what counts as a group.
    """
    return bool(getattr(entity, "title", None))


def is_migrated_chat(entity: Any) -> bool:
    """Return True for a basic ``Chat`` that was migrated to a supergroup.

    Telethon's ``Chat`` exposes ``migrated_to`` as ``None`` for a live
    basic chat and as an ``InputChannel`` pointing at the replacement
    supergroup once migration has happened. ``ChatForbidden`` and
    ``Channel``/``ChannelForbidden`` never carry this attribute, so a
    missing attribute reads as "not migrated" — safe to call on any
    peer-like object.
    """
    return getattr(entity, "migrated_to", None) is not None
