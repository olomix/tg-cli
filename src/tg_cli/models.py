"""Dataclasses for CLI JSON output.

Each model exposes ``to_dict()`` producing a plain ``dict`` suitable for
``json.dumps``. The shapes here are the public contract consumed by the
Claude Code skill and must remain backward compatible.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Group:
    """A Telegram dialog surfaced by ``tg groups``.

    ``type`` is one of ``"group"`` (small group), ``"supergroup"``
    (megagroup channel), or ``"channel"`` (broadcast channel).
    """

    id: int
    title: str
    type: str
    username: str | None
    member_count: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Message:
    """A Telegram message surfaced by ``tg messages`` / ``search`` /
    ``thread`` / ``get``.

    ``date`` is a timezone-aware UTC ``datetime`` and is rendered as an
    ISO-8601 string in :meth:`to_dict` for stable JSON output.
    """

    id: int
    date: datetime
    sender_id: int | None
    sender_name: str | None
    text: str
    reply_to_id: int | None
    group_id: int
    sender_username: str | None = None
    topic_id: int | None = None
    media_kind: str | None = None
    grouped_id: int | None = None
    urls: list[str] = field(default_factory=list)
    forward: dict[str, Any] | None = None
    link: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date.isoformat(),
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "text": self.text,
            "reply_to_id": self.reply_to_id,
            "group_id": self.group_id,
            "sender_username": self.sender_username,
            "topic_id": self.topic_id,
            "media_kind": self.media_kind,
            "grouped_id": self.grouped_id,
            "urls": self.urls,
            "forward": self.forward,
            "link": self.link,
        }
