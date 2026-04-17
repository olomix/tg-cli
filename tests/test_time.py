"""Tests for the ``commands._time`` time-string parser."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tg_cli.commands import _time

_NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=timezone.utc)


def test_parse_returns_utc_datetime_for_relative_hours() -> None:
    result = _time.parse("24h", now=_NOW)
    assert result == _NOW - timedelta(hours=24)
    assert result.tzinfo == timezone.utc


def test_parse_handles_relative_days_and_weeks() -> None:
    assert _time.parse("7d", now=_NOW) == _NOW - timedelta(days=7)
    assert _time.parse("2w", now=_NOW) == _NOW - timedelta(weeks=2)


def test_parse_handles_relative_minutes_and_seconds() -> None:
    assert _time.parse("45m", now=_NOW) == _NOW - timedelta(minutes=45)
    assert _time.parse("30s", now=_NOW) == _NOW - timedelta(seconds=30)


def test_parse_iso_date_becomes_midnight_utc() -> None:
    assert _time.parse("2026-04-15") == datetime(
        2026, 4, 15, tzinfo=timezone.utc
    )


def test_parse_iso_datetime_naive_assumed_utc() -> None:
    assert _time.parse("2026-04-15T10:00") == datetime(
        2026, 4, 15, 10, 0, tzinfo=timezone.utc
    )
    assert _time.parse("2026-04-15T10:00:30") == datetime(
        2026, 4, 15, 10, 0, 30, tzinfo=timezone.utc
    )


def test_parse_iso_datetime_with_offset_normalised_to_utc() -> None:
    # +02:00 is two hours ahead of UTC -> equivalent UTC time is two earlier.
    assert _time.parse("2026-04-15T10:00+02:00") == datetime(
        2026, 4, 15, 8, 0, tzinfo=timezone.utc
    )


def test_parse_strips_surrounding_whitespace() -> None:
    assert _time.parse("  24h  ", now=_NOW) == _NOW - timedelta(hours=24)


def test_parse_uses_real_now_when_omitted() -> None:
    before = datetime.now(timezone.utc) - timedelta(seconds=1)
    parsed = _time.parse("1h")
    after = datetime.now(timezone.utc)
    assert before - timedelta(hours=1) <= parsed <= after - timedelta(
        hours=1, seconds=-1
    )


def test_parse_naive_now_is_treated_as_utc() -> None:
    naive_now = datetime(2026, 4, 17, 12, 0, 0)
    result = _time.parse("1h", now=naive_now)
    assert result == datetime(2026, 4, 17, 11, 0, tzinfo=timezone.utc)


def test_parse_rejects_empty_input() -> None:
    with pytest.raises(_time.TimeParseError):
        _time.parse("")
    with pytest.raises(_time.TimeParseError):
        _time.parse("   ")


def test_parse_rejects_zero_relative_duration() -> None:
    with pytest.raises(_time.TimeParseError):
        _time.parse("0h")


@pytest.mark.parametrize(
    "value", ["abc", "10x", "yesterday", "2026/04/15", "h24", "10"]
)
def test_parse_rejects_unknown_format(value: str) -> None:
    with pytest.raises(_time.TimeParseError):
        _time.parse(value)


def test_parse_rejects_non_string_input() -> None:
    with pytest.raises(_time.TimeParseError):
        _time.parse(None)  # type: ignore[arg-type]
