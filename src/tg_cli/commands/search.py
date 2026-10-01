"""``tg search`` — full-text search within a Telegram group as JSON."""

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


@click.command()
@click.argument("group")
@click.argument("query")
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
    help="Maximum number of matches to return.",
)
@click.option(
    "--pretty",
    is_flag=True,
    help="Indent JSON output for human reading.",
)
@handle_errors
def search(
    group: str, query: str, since: str | None, limit: int, pretty: bool
) -> None:
    """Full-text search QUERY within GROUP, newest first, as JSON."""
    result = asyncio.run(_run_search(group, query, since, limit))
    payload = [m.to_dict() for m in result]
    click.echo(json.dumps(payload, indent=2 if pretty else None))


async def _run_search(
    group: str, query: str, since: str | None, limit: int
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
        # Iterate newest-first so ``--limit`` caps to the most recent
        # matches (not the earliest). With ``--since``, stop when a
        # match predates the cutoff; search output stays newest-first.
        async for raw in client.iter_messages(
            entity,
            limit=limit,
            search=query,
        ):
            if offset_date is not None and _is_older_than(raw, offset_date):
                break
            collected.append(to_message(raw, group_id, link_base=link_base))
        return collected
    finally:
        await client.disconnect()


def _is_older_than(raw: object, cutoff: datetime) -> bool:
    """Return ``True`` when ``raw.date`` is strictly older than ``cutoff``."""
    raw_date = getattr(raw, "date", None)
    if raw_date is None:
        return False
    if raw_date.tzinfo is None:
        raw_date = raw_date.replace(tzinfo=timezone.utc)
    return raw_date < cutoff
