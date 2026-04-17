"""``tg messages`` — fetch recent messages from a Telegram group as JSON."""

from __future__ import annotations

import asyncio
import json
from datetime import timezone
from typing import Any

import click

from ..client import make_client
from ..errors import AuthError, handle_errors
from ..models import Message
from ._resolve import resolve
from ._time import parse as parse_time


@click.command()
@click.argument("group")
@click.option(
    "--since",
    "since",
    default=None,
    help="Only include messages newer than this time "
    "(e.g. 24h, 7d, 2026-04-15, 2026-04-15T10:00).",
)
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=100,
    show_default=True,
    help="Maximum number of messages to return.",
)
@click.option(
    "--pretty",
    is_flag=True,
    help="Indent JSON output for human reading.",
)
@handle_errors
def messages(
    group: str, since: str | None, limit: int, pretty: bool
) -> None:
    """Fetch recent messages from GROUP, oldest first, as JSON."""
    result = asyncio.run(_collect_messages(group, since, limit))
    payload = [m.to_dict() for m in result]
    click.echo(json.dumps(payload, indent=2 if pretty else None))


async def _collect_messages(
    group: str, since: str | None, limit: int
) -> list[Message]:
    offset_date = parse_time(since) if since else None
    client = make_client()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise AuthError()
        entity = await resolve(client, group)
        group_id = int(getattr(entity, "id", 0))
        collected: list[Message] = []
        # ``reverse=True`` makes Telethon yield oldest→newest, which is
        # what callers expect for digest-style consumption.
        async for raw in client.iter_messages(
            entity,
            limit=limit,
            offset_date=offset_date,
            reverse=True,
        ):
            collected.append(_to_message(raw, group_id))
        return collected
    finally:
        await client.disconnect()


def _to_message(raw: Any, group_id: int) -> Message:
    """Build a :class:`Message` from a Telethon ``Message`` object."""
    sender = getattr(raw, "sender", None)
    sender_id = getattr(raw, "sender_id", None)
    if sender_id is not None:
        sender_id = int(sender_id)

    date = getattr(raw, "date", None)
    if date is not None and date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)

    reply_to_id = _reply_to_id(raw)

    return Message(
        id=int(getattr(raw, "id", 0)),
        date=date,
        sender_id=sender_id,
        sender_name=_sender_display_name(sender),
        text=_message_text(raw),
        reply_to_id=reply_to_id,
        group_id=group_id,
    )


def _message_text(raw: Any) -> str:
    text = getattr(raw, "message", None)
    if text is None:
        text = getattr(raw, "text", None)
    return str(text) if text else ""


def _reply_to_id(raw: Any) -> int | None:
    reply_to = getattr(raw, "reply_to", None)
    if reply_to is None:
        return None
    rid = getattr(reply_to, "reply_to_msg_id", None)
    return int(rid) if rid is not None else None


def _sender_display_name(sender: Any) -> str | None:
    if sender is None:
        return None
    title = getattr(sender, "title", None)
    if title:
        return str(title)
    parts = [
        str(p)
        for p in (
            getattr(sender, "first_name", None),
            getattr(sender, "last_name", None),
        )
        if p
    ]
    if parts:
        return " ".join(parts)
    username = getattr(sender, "username", None)
    return str(username) if username else None
