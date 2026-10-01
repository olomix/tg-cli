"""``tg thread`` — fetch a message and its reply thread as JSON."""

from __future__ import annotations

import asyncio
import json

import click
from telethon import errors as telethon_errors

from ..client import make_client
from ..errors import AuthError, MessageNotFoundError, handle_errors
from ..models import Message
from ._message import to_message
from ._peer import marked_peer_id, message_link_base
from ._resolve import resolve


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
        group_id = marked_peer_id(entity)
        link_base = message_link_base(entity)

        # Telethon signals bad/nonexistent message ids via
        # ``MsgIdInvalidError`` and unreachable peers via
        # ``PeerIdInvalidError``; both must surface as the documented
        # ``MessageNotFoundError`` rather than leak a generic RPC error.
        try:
            root_raw = await client.get_messages(entity, ids=message_id)
        except (
            telethon_errors.MsgIdInvalidError,
            telethon_errors.PeerIdInvalidError,
        ) as exc:
            raise MessageNotFoundError(
                f"message {message_id} not found in {group!r}"
            ) from exc
        if root_raw is None:
            raise MessageNotFoundError(
                f"message {message_id} not found in {group!r}"
            )

        collected: list[Message] = [
            to_message(root_raw, group_id, link_base=link_base)
        ]
        # ``reverse=True`` yields replies oldest→newest, matching the
        # chronological ordering readers expect in a thread view.
        # Telethon raises ``MsgIdInvalidError`` / ``PeerIdInvalidError``
        # when ``reply_to`` is used in a chat that does not support reply
        # threads (broadcast-only channels, DMs). Surface that as a clean
        # ``MessageNotFoundError`` so the JSON-error contract holds.
        try:
            async for raw in client.iter_messages(
                entity,
                limit=limit,
                reply_to=message_id,
                reverse=True,
            ):
                collected.append(
                    to_message(raw, group_id, link_base=link_base)
                )
        except (
            telethon_errors.MsgIdInvalidError,
            telethon_errors.PeerIdInvalidError,
        ) as exc:
            raise MessageNotFoundError(
                f"message {message_id} has no reply thread in {group!r}"
            ) from exc
        return collected
    finally:
        await client.disconnect()
