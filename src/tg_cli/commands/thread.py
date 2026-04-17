"""``tg thread`` — fetch a message and its reply thread as JSON."""

from __future__ import annotations

import asyncio
import json

import click

from ..client import make_client
from ..errors import AuthError, MessageNotFoundError, handle_errors
from ..models import Message
from ._resolve import resolve
from .messages import _to_message


@click.command()
@click.argument("group")
@click.argument("message_id", type=int)
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=100,
    show_default=True,
    help="Maximum number of replies to return (root is always included).",
)
@click.option(
    "--pretty",
    is_flag=True,
    help="Indent JSON output for human reading.",
)
@handle_errors
def thread(
    group: str, message_id: int, limit: int, pretty: bool
) -> None:
    """Fetch MESSAGE_ID and its replies from GROUP, root first, as JSON."""
    result = asyncio.run(_collect_thread(group, message_id, limit))
    payload = [m.to_dict() for m in result]
    click.echo(json.dumps(payload, indent=2 if pretty else None))


async def _collect_thread(
    group: str, message_id: int, limit: int
) -> list[Message]:
    client = make_client()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise AuthError()
        entity = await resolve(client, group)
        group_id = int(getattr(entity, "id", 0))

        root_raw = await client.get_messages(entity, ids=message_id)
        if root_raw is None:
            raise MessageNotFoundError(
                f"message {message_id} not found in {group!r}"
            )

        collected: list[Message] = [_to_message(root_raw, group_id)]
        # ``reverse=True`` yields replies oldest→newest, matching the
        # chronological ordering readers expect in a thread view.
        async for raw in client.iter_messages(
            entity,
            limit=limit,
            reply_to=message_id,
            reverse=True,
        ):
            collected.append(_to_message(raw, group_id))
        return collected
    finally:
        await client.disconnect()
