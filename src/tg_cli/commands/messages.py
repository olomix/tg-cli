"""``tg messages`` — fetch recent messages from a Telegram group as JSON."""

from __future__ import annotations

import asyncio
import json

import click

from ..client import make_client
from ..errors import AuthError, handle_errors
from ..models import Message
from ._message import to_message
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
        if offset_date is not None:
            # ``reverse=True`` + ``offset_date`` yields oldest→newest from
            # ``offset_date`` onward, which matches the documented order.
            async for raw in client.iter_messages(
                entity,
                limit=limit,
                offset_date=offset_date,
                reverse=True,
            ):
                collected.append(to_message(raw, group_id))
        else:
            # Without a cutoff, ``reverse=True`` would start at the very
            # beginning of the chat. Fetch newest→oldest, then reverse to
            # keep the documented oldest-first output.
            async for raw in client.iter_messages(entity, limit=limit):
                collected.append(to_message(raw, group_id))
            collected.reverse()
        return collected
    finally:
        await client.disconnect()
