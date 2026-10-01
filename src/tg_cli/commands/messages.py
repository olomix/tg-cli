"""``tg messages`` — fetch recent messages from a Telegram group as JSON."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import click

from ..client import make_client
from ..errors import AuthError, handle_errors
from ..models import Message
from ._message import to_message
from ._peer import marked_peer_id, message_link_base
from ._resolve import resolve
from ._time import parse as parse_time

# Telegram message ids are 32-bit; Telethon raises ``struct.error``
# instead of an RPC error for anything larger.
_MAX_MESSAGE_ID = 2**31 - 1
_MESSAGE_ID = click.IntRange(min=0, max=_MAX_MESSAGE_ID)


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
    "--after-id",
    "after_id",
    type=_MESSAGE_ID,
    default=None,
    help="Only include messages with an id greater than this, reading "
    "the oldest --limit of them. Cannot be combined with --since.",
)
@click.option(
    "--through-id",
    "through_id",
    type=_MESSAGE_ID,
    default=None,
    help="Only include messages with an id up to and including this. "
    "Requires --after-id.",
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
    group: str,
    since: str | None,
    after_id: int | None,
    through_id: int | None,
    limit: int,
    pretty: bool,
) -> None:
    """Fetch recent messages from GROUP, oldest first, as JSON."""
    if after_id is not None and since is not None:
        raise click.UsageError("--after-id cannot be used with --since.")
    if through_id is not None and after_id is None:
        raise click.UsageError("--through-id requires --after-id.")
    result = asyncio.run(
        _collect_messages(group, since, limit, after_id, through_id)
    )
    payload = [m.to_dict() for m in result]
    click.echo(json.dumps(payload, indent=2 if pretty else None))


async def _collect_messages(
    group: str,
    since: str | None,
    limit: int,
    after_id: int | None,
    through_id: int | None,
) -> list[Message]:
    offset_date = parse_time(since) if since else None
    client = make_client()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise AuthError()
        entity = await resolve(client, group)
        group_id = marked_peer_id(entity)
        link_base = message_link_base(entity)
        collected: list[Message] = []
        if after_id is not None:
            if _is_empty_id_range(after_id, through_id):
                return []
            bounds = {"min_id": after_id}
            last_id = _MAX_MESSAGE_ID
            if through_id is not None:
                # Telethon's ``max_id`` is exclusive.
                bounds["max_id"] = through_id + 1
                last_id = through_id
            async for raw in client.iter_messages(
                entity, limit=limit, reverse=True, **bounds
            ):
                collected.append(
                    to_message(raw, group_id, link_base=link_base)
                )
                # Telethon's next request starts at ``raw.id + 1``, which
                # overflows the 32-bit field after the largest id.
                if raw.id >= last_id:
                    break
            return collected
        # Iterate newest-first so ``--limit`` caps to the most recent
        # messages (not the earliest ones). With ``--since``, stop once
        # we cross the cutoff; the list is then reversed for the
        # documented oldest-first output.
        async for raw in client.iter_messages(entity, limit=limit):
            if offset_date is not None and _is_older_than(raw, offset_date):
                break
            collected.append(to_message(raw, group_id, link_base=link_base))
        collected.reverse()
        return collected
    finally:
        await client.disconnect()


def _is_empty_id_range(after_id: int, through_id: int | None) -> bool:
    # Telethon requests from ``after_id + 1``, which overflows the
    # 32-bit field at the largest id; no message can follow it anyway.
    if after_id >= _MAX_MESSAGE_ID:
        return True
    return through_id is not None and through_id <= after_id


def _is_older_than(raw: object, cutoff: datetime) -> bool:
    """Return ``True`` when ``raw.date`` is strictly older than ``cutoff``.

    Telethon dates are normally tz-aware UTC, but service messages and
    some edge-case payloads can carry naive datetimes; treat those as
    UTC to match the rest of the pipeline.
    """
    raw_date = getattr(raw, "date", None)
    if raw_date is None:
        return False
    if raw_date.tzinfo is None:
        raw_date = raw_date.replace(tzinfo=timezone.utc)
    return raw_date < cutoff
