"""``tg groups`` — list Telegram dialogs as JSON."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import click
from telethon.tl import types as _tl

from ..client import make_client
from ..errors import AuthError, handle_errors
from ..models import Group
from ._peer import is_migrated_chat, marked_peer_id

_TYPE_CHOICES = ("group", "channel", "all")


@click.command()
@click.option(
    "--type",
    "type_filter",
    type=click.Choice(_TYPE_CHOICES),
    default="all",
    show_default=True,
    help="Filter by dialog kind.",
)
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=None,
    help="Maximum number of dialogs to return.",
)
@click.option(
    "--pretty",
    is_flag=True,
    help="Indent JSON output for human reading.",
)
@handle_errors
def groups(type_filter: str, limit: int | None, pretty: bool) -> None:
    """List your Telegram groups and channels as JSON."""
    result = asyncio.run(_collect_groups(type_filter, limit))
    payload = [g.to_dict() for g in result]
    click.echo(json.dumps(payload, indent=2 if pretty else None))


async def _collect_groups(
    type_filter: str, limit: int | None
) -> list[Group]:
    client = make_client()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise AuthError()
        collected: list[Group] = []
        async for dialog in client.iter_dialogs():
            group = _dialog_to_group(dialog)
            if group is None:
                continue
            if not _matches_filter(group.type, type_filter):
                continue
            collected.append(group)
            if limit is not None and len(collected) >= limit:
                break
        return collected
    finally:
        await client.disconnect()


def _matches_filter(group_type: str, type_filter: str) -> bool:
    if type_filter == "all":
        return True
    if type_filter == "group":
        return group_type in ("group", "supergroup")
    if type_filter == "channel":
        return group_type == "channel"
    return False


def _dialog_to_group(dialog: Any) -> Group | None:
    """Convert a Telethon ``Dialog`` to a :class:`Group`.

    Returns ``None`` for non-group dialogs (DMs, bots) so the caller can
    filter them out.
    """
    entity = dialog.entity
    kind = _classify(entity)
    if kind is None:
        return None
    # Migrated basic chats are zombie dialogs: their messages now live
    # in the replacement supergroup (which appears separately in the
    # dialog list). Drop them so the listing only shows live peers.
    if is_migrated_chat(entity):
        return None
    return Group(
        id=marked_peer_id(entity),
        title=_entity_title(entity, dialog),
        type=kind,
        username=getattr(entity, "username", None),
        member_count=getattr(entity, "participants_count", None),
    )


_CHANNEL_TYPES: tuple[type, ...] = (_tl.Channel, _tl.ChannelForbidden)


def _classify(entity: Any) -> str | None:
    """Return ``"group"``/``"supergroup"``/``"channel"`` or ``None``.

    User entities (DMs, bots) lack a ``title`` attribute and return
    ``None``. Real Telethon ``Channel`` and ``ChannelForbidden``
    instances are always treated as channel variants (supergroup vs
    broadcast/gigagroup); any other titled entity is a plain ``Chat``
    (small group).
    """
    if getattr(entity, "title", None) is None:
        return None
    if isinstance(entity, _CHANNEL_TYPES):
        return "supergroup" if entity.megagroup else "channel"
    # Flag-based fallback so ``SimpleNamespace`` test doubles still work.
    if getattr(entity, "megagroup", False):
        return "supergroup"
    if getattr(entity, "broadcast", False) or getattr(
        entity, "gigagroup", False
    ):
        return "channel"
    return "group"


def _entity_title(entity: Any, dialog: Any) -> str:
    title = getattr(entity, "title", None)
    if title:
        return str(title)
    name = getattr(dialog, "name", None)
    return str(name) if name is not None else ""
