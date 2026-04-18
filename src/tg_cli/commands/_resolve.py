"""Resolve user-supplied group references to a Telethon entity.

Accepted forms (in priority order):

1. **Numeric id** — ``"-1001234567890"`` or ``"1234"``.
2. **Username** — ``"@mydevgroup"`` (the leading ``@`` is required to
   disambiguate from a substring search; usernames may collide with
   short titles).
3. **Title substring** — case-insensitive, matched against every
   group/channel title returned by ``client.iter_dialogs()``. Raises
   :class:`AmbiguousGroupError` when more than one dialog matches.

The resolver is deliberately strict: silent best-guess matches would
make Claude-driven workflows non-deterministic.
"""

from __future__ import annotations

from typing import Any

from telethon.errors import (
    ChannelInvalidError,
    ChannelPrivateError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)

from tg_cli.commands._peer import _is_migrated_chat


class GroupResolveError(Exception):
    """Base error for group resolution failures."""


class GroupNotFoundError(GroupResolveError):
    """Raised when no dialog matches the given reference."""


class AmbiguousGroupError(GroupResolveError):
    """Raised when more than one dialog matches a title substring."""

    def __init__(self, query: str, matches: list[str]) -> None:
        super().__init__(
            f"ambiguous group {query!r}; matched: " + ", ".join(matches)
        )
        self.query = query
        self.matches = matches


async def resolve(client: Any, reference: str) -> Any:
    """Return the Telethon entity matching ``reference``.

    Numeric ids and ``@usernames`` are resolved via
    ``client.get_entity`` so we don't pay an ``iter_dialogs`` walk for
    the precise cases. Title substrings require iterating dialogs.

    The result is always a group/chat/channel entity. ``User`` entities
    (DMs, bots) — even when an id or ``@handle`` resolves to one — are
    rejected as ``GroupNotFoundError`` so the skill contract that ``tg``
    operates on groups is preserved.
    """
    if not isinstance(reference, str) or not reference.strip():
        raise GroupResolveError("group reference must be a non-empty string")

    text = reference.strip()

    # Telethon raises ``ValueError("Cannot find any entity ...")`` for
    # unknown ids and ``UsernameNotOccupiedError`` / ``UsernameInvalidError``
    # for bad ``@handles``. Wrap both so the skill's JSON-error contract
    # holds: a typo like ``tg messages @nope`` must not leak a traceback.
    if _looks_like_int(text):
        return await _get_entity_or_not_found(client, int(text), reference)

    if text.startswith("@"):
        return await _get_entity_or_not_found(client, text, reference)

    return await _resolve_by_title(client, text)


async def _get_entity_or_not_found(
    client: Any, key: Any, reference: str
) -> Any:
    try:
        entity = await client.get_entity(key)
    except _ENTITY_LOOKUP_ERRORS as exc:
        raise GroupNotFoundError(
            f"no group matched {reference!r}"
        ) from exc
    entity = await _maybe_follow_migration(client, entity, reference)
    if not _is_group_entity(entity):
        raise GroupNotFoundError(
            f"no group matched {reference!r} (resolved to a non-group "
            "entity such as a DM or bot)"
        )
    return entity


async def _maybe_follow_migration(
    client: Any, entity: Any, reference: str
) -> Any:
    """Follow ``Chat.migrated_to`` to the replacement supergroup.

    Old basic-chat ids (copy-pasted from previous listings or memory)
    must transparently resolve to the new supergroup so downstream
    commands don't silently return an empty history. Returns ``entity``
    unchanged when it is not a migrated chat.
    """
    if not _is_migrated_chat(entity):
        return entity
    try:
        return await client.get_entity(entity.migrated_to)
    except _MIGRATION_FOLLOW_ERRORS as exc:
        # ``access_hash`` embedded in ``migrated_to`` may be stale across
        # sessions; Telethon signals that via ``ChannelInvalidError`` /
        # ``ChannelPrivateError`` rather than a plain ``ValueError``.
        raise GroupNotFoundError(
            f"group {reference!r} was migrated to a supergroup that "
            "could not be resolved (run `tg groups` to find its new id)"
        ) from exc


def _is_group_entity(entity: Any) -> bool:
    """Return ``True`` for ``Chat``/``Channel`` entities.

    Telethon ``User`` objects (DMs, bots) lack a ``title`` attribute.
    We mirror the same shape-test ``commands.groups`` uses to filter
    DMs out of dialog listings, keeping resolver and lister consistent.
    """
    return bool(getattr(entity, "title", None))


_ENTITY_LOOKUP_ERRORS = (
    ValueError,
    UsernameNotOccupiedError,
    UsernameInvalidError,
)


_MIGRATION_FOLLOW_ERRORS = (
    ValueError,
    ChannelInvalidError,
    ChannelPrivateError,
)


def _looks_like_int(text: str) -> bool:
    if text.startswith(("+", "-")):
        return text[1:].isdigit()
    return text.isdigit()


async def _resolve_by_title(client: Any, query: str) -> Any:
    needle = query.casefold()
    matches: list[tuple[str, Any]] = []
    async for dialog in client.iter_dialogs():
        title = _dialog_title(dialog)
        if title is None:
            continue
        if needle in title.casefold():
            matches.append((title, dialog.entity))

    if not matches:
        raise GroupNotFoundError(
            f"no group matched {query!r} (searched all dialogs)"
        )
    if len(matches) > 1:
        raise AmbiguousGroupError(query, [t for t, _ in matches])
    return await _maybe_follow_migration(client, matches[0][1], reference=query)


def _dialog_title(dialog: Any) -> str | None:
    """Return the title of a group/chat/channel dialog or ``None``.

    Deliberately does **not** fall back to ``dialog.name`` — that field
    is populated for ``User`` (DM/bot) dialogs too, which would let the
    title-substring path silently match a DM and violate the skill
    contract that ``<group>`` references a group.
    """
    entity = getattr(dialog, "entity", None)
    title = getattr(entity, "title", None)
    return str(title) if title else None
