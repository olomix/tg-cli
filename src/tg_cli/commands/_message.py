"""Telethon message → :class:`tg_cli.models.Message` conversion.

Shared by ``tg messages`` / ``tg search`` / ``tg thread`` so the JSON
output shape is identical regardless of how the message was fetched.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models import Message


def to_message(raw: Any, group_id: int) -> Message:
    """Build a :class:`Message` from a Telethon ``Message`` object.

    Service messages and anonymous-admin edge cases can produce missing
    or non-integer attributes (e.g. ``sender_id`` set to a ``PeerChannel``
    object, or ``date`` unset). Fall back to safe defaults so the JSON
    error contract holds instead of crashing mid-batch.
    """
    sender = getattr(raw, "sender", None)
    sender_id_raw = getattr(raw, "sender_id", None)
    sender_id: int | None
    if sender_id_raw is None:
        sender_id = None
    else:
        try:
            sender_id = int(sender_id_raw)
        except (TypeError, ValueError):
            sender_id = None

    date = getattr(raw, "date", None)
    if date is None:
        date = datetime.fromtimestamp(0, tz=timezone.utc)
    elif date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)

    return Message(
        id=int(getattr(raw, "id", 0)),
        date=date,
        sender_id=sender_id,
        sender_name=_sender_display_name(sender),
        text=_message_text(raw),
        reply_to_id=_reply_to_id(raw),
        group_id=group_id,
        sender_username=_sender_username(sender),
        topic_id=_topic_id(raw),
        grouped_id=_grouped_id(raw),
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
    return _sender_username(sender)


def _sender_username(sender: Any) -> str | None:
    username = getattr(sender, "username", None)
    return str(username) if username else None


def _topic_id(raw: Any) -> int | None:
    reply_to = getattr(raw, "reply_to", None)
    if not getattr(reply_to, "forum_topic", False):
        return None
    # ``is not None`` rather than truthiness: a top id of 0 must not
    # fall through to the replied-to message id.
    for attr in ("reply_to_top_id", "reply_to_msg_id"):
        value = getattr(reply_to, attr, None)
        if value is not None:
            return int(value)
    return None


def _grouped_id(raw: Any) -> int | None:
    grouped_id = getattr(raw, "grouped_id", None)
    return int(grouped_id) if grouped_id is not None else None
