"""Telethon message → :class:`tg_cli.models.Message` conversion.

Shared by ``tg messages`` / ``tg search`` / ``tg thread`` so the JSON
output shape is identical regardless of how the message was fetched.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from telethon import utils as _utils
from telethon.tl import types as _tl

from ..models import Message
from ._peer import public_username


def to_message(
    raw: Any, group_id: int, *, link_base: str | None = None
) -> Message:
    """Build a :class:`Message` from a Telethon ``Message`` object.

    ``link_base`` is the group's permalink prefix; without it the
    message gets no ``link``.

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
    else:
        date = _assume_utc(date)

    message_id = int(getattr(raw, "id", 0))
    topic_id = _topic_id(raw)

    return Message(
        id=message_id,
        date=date,
        sender_id=sender_id,
        sender_name=_sender_display_name(sender),
        text=_message_text(raw),
        reply_to_id=_reply_to_id(raw),
        group_id=group_id,
        sender_username=public_username(sender),
        topic_id=topic_id,
        media_kind=_media_kind(raw),
        grouped_id=_grouped_id(raw),
        urls=_urls(raw),
        forward=_forward(raw),
        link=_link(link_base, topic_id, message_id),
    )


def messages_by_id(found: Iterable[Any]) -> dict[int, Any]:
    """Index the answer to a fetch by ids, without its ``None`` entries.

    The answer cannot be paired with the request by position: Telegram
    may leave an id it does not have out of the answer altogether.
    """
    return {raw.id: raw for raw in found if raw is not None}


def _assume_utc(date: datetime) -> datetime:
    if date.tzinfo is None:
        return date.replace(tzinfo=timezone.utc)
    return date


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
    return public_username(sender)


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


def _media_kind(raw: Any) -> str | None:
    # Decided by the outer media type: Telethon's ``Message.photo`` also
    # returns a link preview's image and a "chat photo changed" picture.
    media = getattr(raw, "media", None)
    if media is None:
        return None
    if isinstance(media, _tl.MessageMediaWebPage):
        return "webpage"
    if isinstance(media, _tl.MessageMediaPhoto):
        return "photo"
    if isinstance(media, _tl.MessageMediaDocument):
        return _document_kind(media.document)
    if isinstance(media, _tl.MessageMediaPoll):
        return "poll"
    return "other"


def _document_kind(document: Any) -> str:
    # An expired or empty document has no attributes at all.
    attributes = getattr(document, "attributes", None) or []

    def find(attribute_type: type) -> Any:
        return next(
            (a for a in attributes if isinstance(a, attribute_type)), None
        )

    # Stickers and animations also carry a video attribute, so they are
    # checked before it.
    if find(_tl.DocumentAttributeSticker) is not None:
        return "sticker"
    if find(_tl.DocumentAttributeAnimated) is not None:
        return "gif"
    if find(_tl.DocumentAttributeVideo) is not None:
        return "video"
    audio = find(_tl.DocumentAttributeAudio)
    if audio is not None:
        return "voice" if audio.voice else "audio"
    return "document"


def _urls(raw: Any) -> list[str]:
    urls: list[str] = []
    for entity in getattr(raw, "entities", None) or []:
        if isinstance(entity, _tl.MessageEntityTextUrl):
            urls.append(entity.url)
        elif isinstance(entity, _tl.MessageEntityUrl):
            # Offsets are UTF-16 units into ``message``; ``text`` is
            # re-rendered as markdown and no longer matches them.
            urls.extend(_utils.get_inner_text(raw.message, [entity]))
    return list(dict.fromkeys(urls))


def _forward(raw: Any) -> dict[str, Any] | None:
    header = getattr(raw, "fwd_from", None)
    if header is None:
        return None
    origin = header.from_id
    date = header.date
    return {
        "from_id": None if origin is None else _utils.get_peer_id(origin),
        "from_name": header.from_name,
        "date": None if date is None else _assume_utc(date).isoformat(),
    }


def _link(
    link_base: str | None, topic_id: int | None, message_id: int
) -> str | None:
    if link_base is None:
        return None
    if topic_id is None:
        return f"{link_base}/{message_id}"
    return f"{link_base}/{topic_id}/{message_id}"
