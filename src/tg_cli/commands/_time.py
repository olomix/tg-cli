"""Time-string parser shared by ``tg messages`` / ``tg search``.

Accepts the formats documented in the README:

* relative: ``24h``, ``7d``, ``45m``, ``2w`` — duration before "now"
* date:     ``2026-04-15``                  — midnight UTC on that day
* datetime: ``2026-04-15T10:00``,
            ``2026-04-15T10:00:00``         — naive ISO becomes UTC

Always returns a timezone-aware ``datetime`` in UTC so callers can pass
the value straight to Telethon's ``offset_date``.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_RELATIVE_RE = re.compile(r"^(?P<n>\d+)(?P<unit>[smhdw])$")

_UNIT_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 60 * 60 * 24,
    "w": 60 * 60 * 24 * 7,
}


class TimeParseError(ValueError):
    """Raised when a time string cannot be interpreted."""


def parse(value: str, *, now: datetime | None = None) -> datetime:
    """Parse a time string into a timezone-aware UTC ``datetime``.

    ``now`` is injectable so tests can pin "now" without monkeypatching.
    Defaults to ``datetime.now(timezone.utc)`` when omitted.
    """
    if not isinstance(value, str) or not value.strip():
        raise TimeParseError("time value must be a non-empty string")

    text = value.strip()
    reference = now if now is not None else datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)

    relative = _RELATIVE_RE.match(text)
    if relative:
        n = int(relative.group("n"))
        unit = relative.group("unit")
        if n == 0:
            raise TimeParseError(
                f"relative duration must be positive: {value!r}"
            )
        delta = timedelta(seconds=n * _UNIT_SECONDS[unit])
        return reference - delta

    parsed = _parse_iso(text)
    if parsed is not None:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        return parsed

    raise TimeParseError(
        f"cannot parse time {value!r}; expected formats: "
        "'24h', '7d', '2026-04-15', '2026-04-15T10:00'"
    )


def _parse_iso(text: str) -> datetime | None:
    """Try ``date.fromisoformat`` then ``datetime.fromisoformat``.

    Returns ``None`` when neither succeeds so the caller can raise a
    single, uniform error message.
    """
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    try:
        from datetime import date

        d = date.fromisoformat(text)
    except ValueError:
        return None
    return datetime(d.year, d.month, d.day)
