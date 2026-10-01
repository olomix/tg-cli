"""``tg get`` — fetch specific messages from a Telegram group as JSON."""

from __future__ import annotations

import asyncio
import json

import click

from ..client import make_client
from ..errors import AuthError, handle_errors
from ..models import Message
from ._message import messages_by_id, to_message
from ._peer import marked_peer_id, message_link_base
from ._resolve import resolve

# Telegram message ids are 32-bit; Telethon raises ``struct.error``
# instead of an RPC error for anything larger.
_MESSAGE_ID = click.IntRange(min=1, max=2**31 - 1)


@click.command()
@click.argument("group")
@click.argument("message_ids", type=_MESSAGE_ID, nargs=-1, required=True)
@click.option(
    "--pretty",
    is_flag=True,
    help="Indent JSON output for human reading.",
)
@handle_errors
def get(group: str, message_ids: tuple[int, ...], pretty: bool) -> None:
    """Fetch the messages MESSAGE_IDS from GROUP, in the order given, as
    JSON. Ids that do not exist are omitted."""
    result = asyncio.run(_collect_by_id(group, list(message_ids)))
    payload = [m.to_dict() for m in result]
    click.echo(json.dumps(payload, indent=2 if pretty else None))


async def _collect_by_id(group: str, message_ids: list[int]) -> list[Message]:
    client = make_client()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise AuthError()
        entity = await resolve(client, group)
        group_id = marked_peer_id(entity)
        link_base = message_link_base(entity)
        found = messages_by_id(
            await client.get_messages(entity, ids=message_ids)
        )
        return [
            to_message(found[message_id], group_id, link_base=link_base)
            for message_id in message_ids
            if message_id in found
        ]
    finally:
        await client.disconnect()
