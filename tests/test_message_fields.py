"""Tests for the fields ``to_message`` extracts from a Telethon message."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from tg_cli.commands._message import to_message

_GROUP_ID = -1001234567890


def _raw(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "id": 7,
        "message": "hello",
        "text": "hello",
        "date": datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc),
        "sender": None,
        "sender_id": None,
        "reply_to": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _reply_header(**fields: Any) -> SimpleNamespace:
    header: dict[str, Any] = {
        "reply_to_msg_id": None,
        "reply_to_top_id": None,
        "forum_topic": False,
    }
    header.update(fields)
    return SimpleNamespace(**header)


def test_sender_username_is_taken_from_the_sender() -> None:
    sender = SimpleNamespace(
        first_name="Alice", last_name="Doe", username="alice"
    )
    msg = to_message(_raw(sender=sender, sender_id=42), _GROUP_ID)
    assert msg.sender_username == "alice"


def test_sender_username_is_none_when_sender_has_no_username() -> None:
    sender = SimpleNamespace(
        first_name="Alice", last_name="Doe", username=None
    )
    msg = to_message(_raw(sender=sender, sender_id=42), _GROUP_ID)
    assert msg.sender_username is None


def test_sender_username_is_none_when_sender_lacks_the_attribute() -> None:
    sender = SimpleNamespace(title="Channel Bot")
    msg = to_message(_raw(sender=sender, sender_id=10), _GROUP_ID)
    assert msg.sender_username is None


def test_sender_username_is_none_without_a_sender() -> None:
    msg = to_message(_raw(sender=None), _GROUP_ID)
    assert msg.sender_username is None


def test_topic_id_is_none_without_a_reply_header() -> None:
    msg = to_message(_raw(reply_to=None), _GROUP_ID)
    assert msg.topic_id is None


def test_topic_id_is_none_when_reply_is_not_in_a_forum_topic() -> None:
    header = _reply_header(
        reply_to_msg_id=41, reply_to_top_id=42, forum_topic=False
    )
    msg = to_message(_raw(reply_to=header), _GROUP_ID)
    assert msg.topic_id is None


def test_topic_id_is_none_when_header_lacks_the_forum_flag() -> None:
    header = SimpleNamespace(reply_to_msg_id=41)
    msg = to_message(_raw(reply_to=header), _GROUP_ID)
    assert msg.topic_id is None


def test_topic_id_uses_the_top_id_of_a_forum_reply() -> None:
    header = _reply_header(reply_to_top_id=42, forum_topic=True)
    msg = to_message(_raw(reply_to=header), _GROUP_ID)
    assert msg.topic_id == 42


def test_topic_id_falls_back_to_the_replied_message_id() -> None:
    header = _reply_header(reply_to_msg_id=42, forum_topic=True)
    msg = to_message(_raw(reply_to=header), _GROUP_ID)
    assert msg.topic_id == 42


def test_topic_id_prefers_the_top_id_over_the_replied_message_id() -> None:
    header = _reply_header(
        reply_to_msg_id=99, reply_to_top_id=42, forum_topic=True
    )
    msg = to_message(_raw(reply_to=header), _GROUP_ID)
    assert msg.topic_id == 42
    assert msg.reply_to_id == 99


def test_topic_id_keeps_a_zero_top_id() -> None:
    header = _reply_header(
        reply_to_msg_id=99, reply_to_top_id=0, forum_topic=True
    )
    msg = to_message(_raw(reply_to=header), _GROUP_ID)
    assert msg.topic_id == 0


def test_grouped_id_is_taken_from_the_message() -> None:
    msg = to_message(_raw(grouped_id=13579246801234567), _GROUP_ID)
    assert msg.grouped_id == 13579246801234567


def test_grouped_id_is_none_when_the_message_has_none() -> None:
    msg = to_message(_raw(grouped_id=None), _GROUP_ID)
    assert msg.grouped_id is None


def test_grouped_id_is_none_when_the_attribute_is_missing() -> None:
    msg = to_message(_raw(), _GROUP_ID)
    assert msg.grouped_id is None
