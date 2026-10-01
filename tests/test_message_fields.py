"""Tests for the fields ``to_message`` extracts from a Telethon message."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from telethon.tl import types

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


def _photo() -> types.Photo:
    return types.Photo(
        id=1,
        access_hash=2,
        file_reference=b"",
        date=None,
        sizes=[types.PhotoSize(type="x", w=800, h=600, size=1000)],
        dc_id=1,
    )


def _document_media(*attributes: Any) -> types.MessageMediaDocument:
    document = types.Document(
        id=1,
        access_hash=2,
        file_reference=b"",
        date=None,
        mime_type="application/octet-stream",
        size=1000,
        dc_id=1,
        attributes=list(attributes),
    )
    return types.MessageMediaDocument(document=document)


def _video_attribute() -> types.DocumentAttributeVideo:
    return types.DocumentAttributeVideo(duration=3, w=640, h=480)


def _sticker_attribute() -> types.DocumentAttributeSticker:
    return types.DocumentAttributeSticker(
        alt="", stickerset=types.InputStickerSetEmpty()
    )


def _webpage_media(photo: Any = None) -> types.MessageMediaWebPage:
    webpage = types.WebPage(
        id=1,
        url="https://example.com/post",
        display_url="example.com/post",
        hash=0,
        photo=photo,
    )
    return types.MessageMediaWebPage(webpage=webpage)


def _poll_media() -> types.MessageMediaPoll:
    poll = types.Poll(
        id=1,
        question=types.TextWithEntities(text="Tabs?", entities=[]),
        answers=[],
        hash=0,
    )
    return types.MessageMediaPoll(poll=poll, results=types.PollResults())


def _plain_url(message: str, url: str) -> types.MessageEntityUrl:
    """Entity covering ``url`` in ``message``, in UTF-16 code units."""

    def utf16_units(text: str) -> int:
        return len(text.encode("utf-16-le")) // 2

    return types.MessageEntityUrl(
        offset=utf16_units(message[: message.index(url)]),
        length=utf16_units(url),
    )


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


@pytest.mark.parametrize(
    ("media", "expected"),
    [
        pytest.param(
            types.MessageMediaPhoto(photo=_photo()), "photo", id="photo"
        ),
        pytest.param(_webpage_media(), "webpage", id="webpage"),
        pytest.param(_poll_media(), "poll", id="poll"),
        pytest.param(
            _document_media(_sticker_attribute()), "sticker", id="sticker"
        ),
        pytest.param(
            _document_media(types.DocumentAttributeAnimated()),
            "gif",
            id="gif",
        ),
        pytest.param(_document_media(_video_attribute()), "video", id="video"),
        pytest.param(
            _document_media(
                types.DocumentAttributeAudio(duration=5, voice=True)
            ),
            "voice",
            id="voice",
        ),
        pytest.param(
            _document_media(types.DocumentAttributeAudio(duration=180)),
            "audio",
            id="audio",
        ),
        pytest.param(
            _document_media(
                types.DocumentAttributeFilename(file_name="report.pdf")
            ),
            "document",
            id="document",
        ),
        pytest.param(
            types.MessageMediaGeo(
                geo=types.GeoPoint(long=30.5, lat=50.4, access_hash=0)
            ),
            "other",
            id="other",
        ),
    ],
)
def test_media_kind_names_the_attached_media(
    media: Any, expected: str
) -> None:
    msg = to_message(_raw(media=media), _GROUP_ID)
    assert msg.media_kind == expected


def test_media_kind_is_none_when_media_is_none() -> None:
    msg = to_message(_raw(media=None), _GROUP_ID)
    assert msg.media_kind is None


def test_media_kind_is_none_when_the_attribute_is_missing() -> None:
    msg = to_message(_raw(), _GROUP_ID)
    assert msg.media_kind is None


def test_media_kind_of_a_link_preview_with_an_image_is_webpage() -> None:
    # ``photo`` mirrors Telethon's ``Message.photo``, which also returns
    # the preview image of a link.
    photo = _photo()
    raw = _raw(media=_webpage_media(photo=photo), photo=photo)
    assert to_message(raw, _GROUP_ID).media_kind == "webpage"


def test_media_kind_of_a_chat_photo_service_message_is_none() -> None:
    # ``photo`` mirrors Telethon's ``Message.photo``, which also returns
    # the picture of a "chat photo changed" action.
    photo = _photo()
    raw = _raw(
        message=None,
        text=None,
        media=None,
        action=types.MessageActionChatEditPhoto(photo=photo),
        photo=photo,
    )
    assert to_message(raw, _GROUP_ID).media_kind is None


def test_media_kind_of_an_animation_with_a_video_attribute_is_gif() -> None:
    media = _document_media(
        _video_attribute(), types.DocumentAttributeAnimated()
    )
    assert to_message(_raw(media=media), _GROUP_ID).media_kind == "gif"


def test_media_kind_of_a_sticker_with_a_video_attribute_is_sticker() -> None:
    media = _document_media(_video_attribute(), _sticker_attribute())
    assert to_message(_raw(media=media), _GROUP_ID).media_kind == "sticker"


def test_media_kind_of_an_expired_photo_is_photo() -> None:
    media = types.MessageMediaPhoto(photo=None)
    assert to_message(_raw(media=media), _GROUP_ID).media_kind == "photo"


def test_media_kind_of_media_without_a_document_is_document() -> None:
    media = types.MessageMediaDocument(document=None)
    assert to_message(_raw(media=media), _GROUP_ID).media_kind == "document"


def test_media_kind_of_an_empty_document_is_document() -> None:
    media = types.MessageMediaDocument(document=types.DocumentEmpty(id=1))
    assert to_message(_raw(media=media), _GROUP_ID).media_kind == "document"


def test_urls_is_empty_without_entities() -> None:
    assert to_message(_raw(entities=None), _GROUP_ID).urls == []


def test_urls_is_empty_when_the_attribute_is_missing() -> None:
    assert to_message(_raw(), _GROUP_ID).urls == []


def test_urls_holds_the_hidden_target_of_a_text_link() -> None:
    raw = _raw(
        message="read the docs",
        entities=[
            types.MessageEntityTextUrl(
                offset=9, length=4, url="https://example.com/docs"
            )
        ],
    )
    assert to_message(raw, _GROUP_ID).urls == ["https://example.com/docs"]


def test_urls_holds_the_text_covered_by_a_plain_url() -> None:
    message = "see https://example.com/a for details"
    raw = _raw(
        message=message,
        entities=[_plain_url(message, "https://example.com/a")],
    )
    assert to_message(raw, _GROUP_ID).urls == ["https://example.com/a"]


def test_urls_reads_a_plain_url_after_a_non_bmp_emoji_intact() -> None:
    # The emoji is one Python character but two UTF-16 code units, so
    # the URL starts at offset 3, not 2.
    raw = _raw(
        message="\U0001f600 https://example.com/a",
        entities=[types.MessageEntityUrl(offset=3, length=21)],
    )
    assert to_message(raw, _GROUP_ID).urls == ["https://example.com/a"]


def test_urls_lists_a_url_once_when_linked_and_written_out() -> None:
    message = "docs: https://example.com/docs"
    raw = _raw(
        message=message,
        entities=[
            types.MessageEntityTextUrl(
                offset=0, length=4, url="https://example.com/docs"
            ),
            _plain_url(message, "https://example.com/docs"),
        ],
    )
    assert to_message(raw, _GROUP_ID).urls == ["https://example.com/docs"]


def test_urls_keep_their_order_of_appearance() -> None:
    message = "https://c.example then link then https://a.example"
    raw = _raw(
        message=message,
        entities=[
            _plain_url(message, "https://c.example"),
            types.MessageEntityTextUrl(
                offset=23, length=4, url="https://b.example"
            ),
            _plain_url(message, "https://a.example"),
        ],
    )
    assert to_message(raw, _GROUP_ID).urls == [
        "https://c.example",
        "https://b.example",
        "https://a.example",
    ]


def test_urls_ignores_other_entity_types() -> None:
    raw = _raw(
        message="@bob bold bob@example.com #tag",
        entities=[
            types.MessageEntityMention(offset=0, length=4),
            types.MessageEntityBold(offset=5, length=4),
            types.MessageEntityEmail(offset=10, length=15),
            types.MessageEntityHashtag(offset=26, length=4),
        ],
    )
    assert to_message(raw, _GROUP_ID).urls == []


def test_urls_are_read_from_the_raw_message_not_the_markdown_text() -> None:
    # ``text`` mirrors Telethon's ``Message.text``, which re-renders the
    # entities as markdown and so shifts every offset after the bold.
    message = "bold https://example.com/x"
    raw = _raw(
        message=message,
        text="**bold** https://example.com/x",
        entities=[
            types.MessageEntityBold(offset=0, length=4),
            _plain_url(message, "https://example.com/x"),
        ],
    )
    assert to_message(raw, _GROUP_ID).urls == ["https://example.com/x"]


def test_forward_is_none_when_the_message_was_not_forwarded() -> None:
    assert to_message(_raw(fwd_from=None), _GROUP_ID).forward is None


def test_forward_is_none_when_the_attribute_is_missing() -> None:
    assert to_message(_raw(), _GROUP_ID).forward is None


def test_forward_from_a_channel_has_its_marked_id_and_iso_date() -> None:
    header = types.MessageFwdHeader(
        date=datetime(2026, 4, 16, 9, 30, tzinfo=timezone.utc),
        from_id=types.PeerChannel(channel_id=1234567890),
    )
    assert to_message(_raw(fwd_from=header), _GROUP_ID).forward == {
        "from_id": -1001234567890,
        "from_name": None,
        "date": "2026-04-16T09:30:00+00:00",
    }


def test_forward_from_a_user_has_the_user_id() -> None:
    header = types.MessageFwdHeader(
        date=datetime(2026, 4, 16, 9, 30, tzinfo=timezone.utc),
        from_id=types.PeerUser(user_id=42),
    )
    forward = to_message(_raw(fwd_from=header), _GROUP_ID).forward
    assert forward is not None
    assert forward["from_id"] == 42


def test_forward_with_only_a_name_has_no_from_id() -> None:
    header = types.MessageFwdHeader(
        date=datetime(2026, 4, 16, 9, 30, tzinfo=timezone.utc),
        from_name="Hidden User",
    )
    assert to_message(_raw(fwd_from=header), _GROUP_ID).forward == {
        "from_id": None,
        "from_name": "Hidden User",
        "date": "2026-04-16T09:30:00+00:00",
    }


def test_forward_treats_a_naive_date_as_utc() -> None:
    header = types.MessageFwdHeader(
        date=datetime(2026, 4, 16, 9, 30), from_name="Hidden User"
    )
    forward = to_message(_raw(fwd_from=header), _GROUP_ID).forward
    assert forward is not None
    assert forward["date"] == "2026-04-16T09:30:00+00:00"


def test_forward_date_is_none_when_the_header_has_no_date() -> None:
    header = types.MessageFwdHeader(date=None, from_name="Hidden User")
    forward = to_message(_raw(fwd_from=header), _GROUP_ID).forward
    assert forward is not None
    assert forward["date"] is None
