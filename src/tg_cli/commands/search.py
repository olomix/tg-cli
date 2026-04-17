"""``tg search`` — full-text search within a Telegram group as JSON."""

from __future__ import annotations

import asyncio
import json

import click

from ..client import make_client
from ..config import ConfigError
from ..models import Message
from ._resolve import GroupResolveError, resolve
from ._time import TimeParseError
from ._time import parse as parse_time
from .messages import _to_message


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
def search(
    group: str, query: str, since: str | None, limit: int, pretty: bool
) -> None:
    """Full-text search QUERY within GROUP, newest first, as JSON."""
    try:
        result = asyncio.run(_run_search(group, query, since, limit))
    except ConfigError as e:
        raise click.ClickException(str(e)) from e
    except TimeParseError as e:
        raise click.ClickException(str(e)) from e
    except GroupResolveError as e:
        raise click.ClickException(str(e)) from e

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
            raise click.ClickException(
                "Not logged in. Run `tg login` first."
            )
        entity = await resolve(client, group)
        group_id = int(getattr(entity, "id", 0))
        collected: list[Message] = []
        # Telethon's search returns newest→oldest; leave order as-is so
        # callers see the most relevant recent hits first.
        async for raw in client.iter_messages(
            entity,
            limit=limit,
            offset_date=offset_date,
            search=query,
        ):
            collected.append(_to_message(raw, group_id))
        return collected
    finally:
        await client.disconnect()
