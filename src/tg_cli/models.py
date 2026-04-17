"""Dataclasses for CLI JSON output.

Each model exposes ``to_dict()`` producing a plain ``dict`` suitable for
``json.dumps``. The shapes here are the public contract consumed by the
Claude Code skill and must remain backward compatible.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
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
