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
    """
    if not isinstance(reference, str) or not reference.strip():
        raise GroupResolveError("group reference must be a non-empty string")

    text = reference.strip()

    if _looks_like_int(text):
        return await client.get_entity(int(text))

    if text.startswith("@"):
        return await client.get_entity(text)

    return await _resolve_by_title(client, text)


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
    return matches[0][1]


def _dialog_title(dialog: Any) -> str | None:
    entity = getattr(dialog, "entity", None)
    title = getattr(entity, "title", None)
    if title:
        return str(title)
    name = getattr(dialog, "name", None)
    return str(name) if name else None
