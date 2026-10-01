"""Tests for the ``tg download`` command and
:class:`tg_cli.models.DownloadResult`."""

from __future__ import annotations

import contextlib
import errno
import json
import os
import stat
import tempfile
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner
from telethon.client.downloads import DownloadMethods
from telethon.errors import FloodWaitError, RPCError
from telethon.tl import types

from tg_cli import cli
from tg_cli.models import DownloadResult

_DAY = datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc)
_MIB = 1024 * 1024


class _AsyncIter:
    def __init__(self, items: Iterable[Any]) -> None:
        self._items = list(items)

    def __aiter__(self) -> _AsyncIter:
        return self

    async def __anext__(self) -> Any:
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


def _size(type_: str, size: int) -> types.PhotoSize:
    return types.PhotoSize(type=type_, w=100, h=100, size=size)


def _progressive(type_: str, *sizes: int) -> types.PhotoSizeProgressive:
    return types.PhotoSizeProgressive(
        type=type_, w=100, h=100, sizes=list(sizes)
    )


def _cached(type_: str, length: int) -> types.PhotoCachedSize:
    return types.PhotoCachedSize(
        type=type_, w=100, h=100, bytes=b"c" * length
    )


def _placeholders() -> list[Any]:
    """Size entries that hold no real image. Their payloads are longer
    than the real variants used next to them, so counting them by
    length would get them picked."""
    return [
        types.PhotoStrippedSize(type="i", bytes=b"s" * 900),
        types.PhotoPathSize(type="j", bytes=b"p" * 900),
        types.PhotoSizeEmpty(type="e"),
    ]


def _photo(*sizes: Any) -> types.Photo:
    return types.Photo(
        id=1,
        access_hash=2,
        file_reference=b"",
        date=None,
        sizes=list(sizes),
        dc_id=1,
    )


def _msg(id: int, media: Any = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        message=f"m{id}",
        text=f"m{id}",
        date=_DAY,
        sender=None,
        sender_id=None,
        reply_to=None,
        media=media,
    )


def _photo_msg(id: int, *sizes: Any) -> SimpleNamespace:
    sizes = sizes or (_size("x", 1000),)
    return _msg(id, types.MessageMediaPhoto(photo=_photo(*sizes)))


def _real_photo_msg(id: int, *sizes: Any) -> types.Message:
    return types.Message(
        id=id,
        peer_id=types.PeerChannel(channel_id=1),
        date=_DAY,
        message="",
        media=types.MessageMediaPhoto(photo=_photo(*sizes)),
    )


def _link_preview_media() -> types.MessageMediaWebPage:
    webpage = types.WebPage(
        id=1,
        url="https://example.com/post",
        display_url="example.com/post",
        hash=0,
        photo=_photo(_size("x", 1000)),
    )
    return types.MessageMediaWebPage(webpage=webpage)


def _document_media() -> types.MessageMediaDocument:
    document = types.Document(
        id=1,
        access_hash=2,
        file_reference=b"",
        date=None,
        mime_type="image/jpeg",
        size=1000,
        dc_id=1,
        attributes=[types.DocumentAttributeFilename(file_name="a.jpg")],
    )
    return types.MessageMediaDocument(document=document)


def _entity(id: int, title: str = "Group") -> SimpleNamespace:
    # Bare positive id + channel flags match real Telethon shape; the
    # command converts this to a marked peer id for the file name.
    return SimpleNamespace(id=id, title=title, megagroup=True, broadcast=False)


def _fake_client(
    *,
    entity: SimpleNamespace,
    stored: Iterable[SimpleNamespace] = (),
    authorized: bool = True,
    payloads: dict[int, bytes] | None = None,
) -> MagicMock:
    """Client whose ``get_messages`` gives the usual answer for a list
    of ids (request order, ``None`` for an id that does not exist), and
    whose ``download_media`` writes the message's payload to the path
    it is given and returns that path."""
    by_id = {m.id: m for m in stored}
    payloads = payloads or {}

    async def get_messages(_entity: Any, *, ids: list[int]) -> list[Any]:
        return [by_id.get(i) for i in ids]

    async def download_media(message: Any, *, file: str, thumb: Any) -> str:
        Path(file).write_bytes(payloads.get(message.id, b"jpeg"))
        return file

    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=authorized)
    client.get_entity = AsyncMock(return_value=entity)
    client.iter_dialogs = MagicMock(return_value=_AsyncIter([]))
    client.get_messages = AsyncMock(side_effect=get_messages)
    client.download_media = AsyncMock(side_effect=download_media)
    return client


def _telethon_download(fetched: bytes = b"") -> tuple[AsyncMock, list[Any]]:
    """``download_media`` that runs Telethon's real one, and the list
    its return values go to. Only the network fetch is replaced: it
    writes ``fetched`` to the file it is given."""
    telethon = DownloadMethods()
    returned = []

    async def download_file(_location: Any, file: str, **_: Any) -> str:
        Path(file).write_bytes(fetched)
        return file

    async def download_media(message: Any, *, file: str, thumb: Any) -> Any:
        path = await telethon.download_media(message, file=file, thumb=thumb)
        returned.append(path)
        return path

    telethon.download_file = AsyncMock(side_effect=download_file)
    return AsyncMock(side_effect=download_media), returned


def _invoke(client: MagicMock, *args: str) -> Any:
    with patch("tg_cli.commands.download.make_client", return_value=client):
        return CliRunner().invoke(cli.main, ["download", *args])


def _invoke_without_client(*args: str) -> tuple[Any, MagicMock]:
    with patch("tg_cli.commands.download.make_client") as make_client:
        result = CliRunner().invoke(cli.main, ["download", *args])
    return result, make_client


def _entries(result: Any) -> list[dict[str, Any]]:
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _summary(result: Any) -> list[tuple[int, str, str | None]]:
    return [(e["id"], e["status"], e["reason"]) for e in _entries(result)]


def _skipped(id: int, reason: str) -> dict[str, Any]:
    return {
        "id": id,
        "status": "skipped",
        "path": None,
        "bytes": None,
        "reason": reason,
    }


def _thumbs(client: MagicMock) -> list[Any]:
    return [
        call.kwargs["thumb"] for call in client.download_media.await_args_list
    ]


def _files(client: MagicMock) -> list[str]:
    return [
        call.kwargs["file"] for call in client.download_media.await_args_list
    ]


def _names(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


@contextlib.contextmanager
def _unlocked_afterwards(directory: Path) -> Iterator[None]:
    """Make ``directory`` writable again once the block ends, however
    it ends, so pytest can remove what a test locked."""
    try:
        yield
    finally:
        directory.chmod(0o700)


def _error(result: Any) -> dict[str, str]:
    assert result.exit_code == 1, result.output
    # Empty stderr means the failure escaped as a traceback.
    assert result.stderr, repr(result.exception)
    assert result.stdout == ""
    return json.loads(result.stderr)


def _usage_error(result: Any) -> str:
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stderr)
    assert payload["type"] == "UsageError"
    return payload["error"]


def _download_one(
    tmp_path: Path, sizes: list[Any], *options: str
) -> tuple[dict[str, Any], MagicMock]:
    client = _fake_client(entity=_entity(1), stored=[_photo_msg(5, *sizes)])
    result = _invoke(client, "--dir", str(tmp_path), *options, "1", "5")
    [entry] = _entries(result)
    return entry, client


# --- results -------------------------------------------------------------


def test_download_result_to_dict_has_the_documented_keys_in_order() -> None:
    result = DownloadResult(
        id=5, status="saved", path="/d/-1_5.jpg", bytes=777, reason=None
    )
    assert list(result.to_dict().items()) == [
        ("id", 5),
        ("status", "saved"),
        ("path", "/d/-1_5.jpg"),
        ("bytes", 777),
        ("reason", None),
    ]


def test_download_saves_a_photo_named_by_group_and_message_id(
    tmp_path: Path,
) -> None:
    # Bare channel id ``1234567890`` → marked peer id ``-1001234567890``.
    entity = _entity(1234567890, "Dev")
    message = _photo_msg(5, _size("x", 1000))
    payload = b"p" * 777
    client = _fake_client(
        entity=entity, stored=[message], payloads={5: payload}
    )
    result = _invoke(client, "--dir", str(tmp_path), "@dev", "5")
    saved = tmp_path / "-1001234567890_5.jpg"
    # ``bytes`` is what landed on disk, not the 1000 the variant declares.
    assert _entries(result) == [
        {
            "id": 5,
            "status": "saved",
            "path": str(saved),
            "bytes": 777,
            "reason": None,
        }
    ]
    assert saved.read_bytes() == payload
    client.get_entity.assert_awaited_once_with("@dev")
    client.get_messages.assert_awaited_once_with(entity, ids=[5])
    assert client.download_media.await_args.args == (message,)


def test_download_reports_an_absolute_path_for_a_relative_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    client = _fake_client(entity=_entity(1), stored=[_photo_msg(5)])
    result = _invoke(client, "--dir", "photos", "1", "5")
    [entry] = _entries(result)
    assert os.path.isabs(entry["path"])
    assert entry["path"] == os.path.join(
        os.getcwd(), "photos", "-1000000000001_5.jpg"
    )
    assert os.path.isfile(entry["path"])


def test_download_reports_a_missing_id_as_not_found(tmp_path: Path) -> None:
    client = _fake_client(entity=_entity(1), stored=[])
    result = _invoke(client, "--dir", str(tmp_path), "1", "9")
    assert _entries(result) == [_skipped(9, "not_found")]
    client.download_media.assert_not_called()
    assert _names(tmp_path) == []


@pytest.mark.parametrize(
    "media",
    [
        pytest.param(None, id="text"),
        pytest.param(_link_preview_media(), id="link-preview"),
        pytest.param(_document_media(), id="document"),
        pytest.param(types.MessageMediaPhoto(photo=None), id="expired-photo"),
        pytest.param(
            types.MessageMediaPhoto(photo=types.PhotoEmpty(id=1)),
            id="empty-photo",
        ),
    ],
)
def test_download_skips_a_message_without_a_photo(
    tmp_path: Path, media: Any
) -> None:
    client = _fake_client(entity=_entity(1), stored=[_msg(5, media)])
    result = _invoke(client, "--dir", str(tmp_path), "1", "5")
    assert _entries(result) == [_skipped(5, "not_photo")]
    client.download_media.assert_not_called()
    assert _names(tmp_path) == []


def test_download_returns_one_entry_per_id_in_request_order(
    tmp_path: Path,
) -> None:
    entity = _entity(1)
    client = _fake_client(
        entity=entity, stored=[_msg(3), _photo_msg(5), _photo_msg(7)]
    )
    result = _invoke(client, "--dir", str(tmp_path), "1", "7", "3", "9", "5")
    assert _summary(result) == [
        (7, "saved", None),
        (3, "skipped", "not_photo"),
        (9, "skipped", "not_found"),
        (5, "saved", None),
    ]
    client.get_messages.assert_awaited_once_with(entity, ids=[7, 3, 9, 5])
    assert _names(tmp_path) == [
        "-1000000000001_5.jpg",
        "-1000000000001_7.jpg",
    ]


def test_download_accepts_negative_group_id_after_separator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    client = _fake_client(
        entity=_entity(1234567890), stored=[_photo_msg(5)]
    )
    result = _invoke(client, "--dir", "d", "--", "-1001234567890", "5")
    [entry] = _entries(result)
    assert entry["status"] == "saved"
    client.get_entity.assert_awaited_once_with(-1001234567890)
    assert (tmp_path / "d" / "-1001234567890_5.jpg").is_file()


def test_download_disconnects_on_success(tmp_path: Path) -> None:
    client = _fake_client(entity=_entity(1), stored=[_photo_msg(5)])
    result = _invoke(client, "--dir", str(tmp_path), "1", "5")
    assert result.exit_code == 0, result.output
    client.connect.assert_awaited_once()
    client.disconnect.assert_awaited_once()


# --- pairing answers with ids --------------------------------------------


def _answer(client: MagicMock, *messages: Any) -> None:
    """Make ``get_messages`` return exactly ``messages``, whatever ids
    it is asked for."""
    client.get_messages = AsyncMock(return_value=list(messages))


def test_download_reports_an_id_telegram_left_out_as_not_found(
    tmp_path: Path,
) -> None:
    client = _fake_client(
        entity=_entity(1), payloads={5: b"five", 7: b"seven"}
    )
    _answer(client, _photo_msg(5), _photo_msg(7))
    result = _invoke(client, "--dir", str(tmp_path), "1", "5", "6", "7")
    assert _summary(result) == [
        (5, "saved", None),
        (6, "skipped", "not_found"),
        (7, "saved", None),
    ]
    assert _names(tmp_path) == [
        "-1000000000001_5.jpg",
        "-1000000000001_7.jpg",
    ]
    assert (tmp_path / "-1000000000001_5.jpg").read_bytes() == b"five"
    assert (tmp_path / "-1000000000001_7.jpg").read_bytes() == b"seven"


def test_download_names_each_file_by_the_id_of_its_own_message(
    tmp_path: Path,
) -> None:
    client = _fake_client(
        entity=_entity(1), payloads={5: b"five", 7: b"seven"}
    )
    _answer(client, _photo_msg(5), None, _photo_msg(7))
    result = _invoke(client, "--dir", str(tmp_path), "1", "7", "6", "5")
    assert _summary(result) == [
        (7, "saved", None),
        (6, "skipped", "not_found"),
        (5, "saved", None),
    ]
    assert (tmp_path / "-1000000000001_5.jpg").read_bytes() == b"five"
    assert (tmp_path / "-1000000000001_7.jpg").read_bytes() == b"seven"


@pytest.mark.parametrize("copies", [1, 2])
def test_download_reports_an_id_requested_twice_twice(
    tmp_path: Path, copies: int
) -> None:
    client = _fake_client(entity=_entity(1), payloads={5: b"five"})
    _answer(client, *[_photo_msg(5)] * copies, _msg(6))
    result = _invoke(client, "--dir", str(tmp_path), "1", "5", "6", "5")
    first, _, second = _entries(result)
    assert first["status"] == "saved"
    assert second == first
    assert _names(tmp_path) == ["-1000000000001_5.jpg"]
    assert (tmp_path / "-1000000000001_5.jpg").read_bytes() == b"five"


def test_download_reports_every_id_of_an_empty_answer_as_not_found(
    tmp_path: Path,
) -> None:
    client = _fake_client(entity=_entity(1))
    _answer(client)
    result = _invoke(client, "--dir", str(tmp_path), "1", "5", "6")
    assert _entries(result) == [
        _skipped(5, "not_found"),
        _skipped(6, "not_found"),
    ]
    client.download_media.assert_not_called()
    assert _names(tmp_path) == []


# --- variant choice ------------------------------------------------------


@pytest.mark.parametrize(
    "max_bytes,expected",
    [("5000", "x"), ("2000", "m"), ("1000", "m"), ("999", "s")],
)
def test_download_picks_the_largest_variant_that_fits(
    tmp_path: Path, max_bytes: str, expected: str
) -> None:
    sizes = [_size("s", 100), _size("x", 5000), _size("m", 1000)]
    entry, client = _download_one(tmp_path, sizes, "--max-bytes", max_bytes)
    assert entry["status"] == "saved"
    assert _thumbs(client) == [expected]


@pytest.mark.parametrize(
    "max_bytes,expected", [("2999", "m"), ("3000", "y")]
)
def test_download_counts_a_progressive_variant_by_its_largest_entry(
    tmp_path: Path, max_bytes: str, expected: str
) -> None:
    sizes = [_size("m", 1000), _progressive("y", 500, 3000, 1500)]
    entry, client = _download_one(tmp_path, sizes, "--max-bytes", max_bytes)
    assert entry["status"] == "saved"
    assert _thumbs(client) == [expected]


@pytest.mark.parametrize(
    "max_bytes,expected", [("1000", "s"), ("299", "m")]
)
def test_download_counts_a_cached_variant_by_the_length_of_its_bytes(
    tmp_path: Path, max_bytes: str, expected: str
) -> None:
    sizes = [_size("m", 200), _cached("s", 300), _size("x", 5000)]
    entry, client = _download_one(tmp_path, sizes, "--max-bytes", max_bytes)
    assert entry["status"] == "saved"
    assert _thumbs(client) == [expected]


def test_download_never_picks_a_placeholder_variant(tmp_path: Path) -> None:
    sizes = [*_placeholders(), _size("m", 500)]
    entry, client = _download_one(tmp_path, sizes)
    assert entry["status"] == "saved"
    assert _thumbs(client) == ["m"]


@pytest.mark.parametrize(
    "real",
    [
        pytest.param(_size("m", 300), id="plain"),
        pytest.param(_progressive("x", 100, 300), id="progressive"),
        pytest.param(_cached("c", 300), id="cached"),
    ],
)
def test_download_through_telethon_ignores_a_progressive_size_without_sizes(
    tmp_path: Path, real: Any
) -> None:
    """Telethon's own choice of size raises on such an entry, so this
    runs its real download too."""
    sizes = [real, _progressive("y")]
    message = _real_photo_msg(5, *sizes)
    client = _fake_client(entity=_entity(1), stored=[message])
    client.download_media, _ = _telethon_download(fetched=b"c" * 300)
    result = _invoke(client, "--dir", str(tmp_path), "1", "5")
    [entry] = _entries(result)
    assert (entry["status"], entry["bytes"]) == ("saved", 300)
    assert Path(entry["path"]).read_bytes() == b"c" * 300
    assert _names(tmp_path) == ["-1000000000001_5.jpg"]
    assert message.media.photo.sizes == sizes


@pytest.mark.parametrize(
    "video_size",
    [
        pytest.param(
            types.VideoSizeEmojiMarkup(emoji_id=1, background_colors=[0]),
            id="emoji-markup",
        ),
        pytest.param(
            types.VideoSizeStickerMarkup(
                stickerset=types.InputStickerSetEmpty(),
                sticker_id=1,
                background_colors=[0],
            ),
            id="sticker-markup",
        ),
    ],
)
def test_download_through_telethon_ignores_a_video_size_markup(
    tmp_path: Path, video_size: Any
) -> None:
    """Telethon looks the size up among the video sizes too, where a
    markup has no type to compare, so this runs its real download."""
    message = _real_photo_msg(5, _cached("c", 300))
    message.media.photo.video_sizes = [video_size]
    client = _fake_client(entity=_entity(1), stored=[message])
    client.download_media, _ = _telethon_download()
    result = _invoke(client, "--dir", str(tmp_path), "1", "5")
    [entry] = _entries(result)
    assert (entry["status"], entry["bytes"]) == ("saved", 300)
    assert Path(entry["path"]).read_bytes() == b"c" * 300
    assert _names(tmp_path) == ["-1000000000001_5.jpg"]
    assert message.media.photo.video_sizes == [video_size]


@pytest.mark.parametrize(
    "sizes",
    [
        pytest.param(_placeholders(), id="placeholders-only"),
        pytest.param([], id="no-sizes"),
        pytest.param([_progressive("y")], id="empty-progressive"),
    ],
)
def test_download_skips_a_photo_without_a_real_variant(
    tmp_path: Path, sizes: list[Any]
) -> None:
    client = _fake_client(
        entity=_entity(1),
        stored=[_msg(5, types.MessageMediaPhoto(photo=_photo(*sizes)))],
    )
    result = _invoke(client, "--dir", str(tmp_path), "1", "5")
    assert _entries(result) == [_skipped(5, "not_photo")]
    client.download_media.assert_not_called()


def test_download_skips_a_photo_whose_variants_all_exceed_the_limit(
    tmp_path: Path,
) -> None:
    sizes = [*_placeholders(), _size("m", 1000), _progressive("y", 500, 3000)]
    entry, client = _download_one(tmp_path, sizes, "--max-bytes", "999")
    assert entry == _skipped(5, "too_large")
    client.download_media.assert_not_called()
    assert _names(tmp_path) == []


def test_download_default_limit_is_five_mebibytes(tmp_path: Path) -> None:
    sizes = [_size("w", 5 * _MIB + 1), _size("x", 5 * _MIB)]
    entry, client = _download_one(tmp_path, sizes)
    assert entry["status"] == "saved"
    assert _thumbs(client) == ["x"]


def test_download_default_limit_rejects_a_larger_photo(tmp_path: Path) -> None:
    entry, client = _download_one(tmp_path, [_size("w", 5 * _MIB + 1)])
    assert entry == _skipped(5, "too_large")
    client.download_media.assert_not_called()


# --- thumb argument ------------------------------------------------------


@pytest.mark.parametrize(
    "chosen",
    [
        pytest.param(_size("x", 3000), id="plain"),
        pytest.param(_progressive("y", 500, 3000), id="progressive"),
        pytest.param(_cached("c", 3000), id="cached"),
    ],
)
def test_download_names_the_variant_by_its_type_string(
    tmp_path: Path, chosen: Any
) -> None:
    sizes = [*_placeholders(), _size("m", 1000), chosen]
    entry, client = _download_one(tmp_path, sizes)
    assert entry["status"] == "saved"
    [thumb] = _thumbs(client)
    assert isinstance(thumb, str)
    assert thumb == chosen.type
    # Telethon must find the same variant from what it was handed; a
    # ``PhotoSizeProgressive`` object passed as-is resolves to ``None``.
    [handed] = client.download_media.await_args.args
    assert DownloadMethods._get_thumb(handed.media.photo.sizes, thumb) is chosen


# --- directory -----------------------------------------------------------


def test_download_creates_a_missing_dir_private_to_the_user(
    tmp_path: Path,
) -> None:
    target = tmp_path / "photos"
    client = _fake_client(entity=_entity(1), stored=[_photo_msg(5)])
    result = _invoke(client, "--dir", str(target), "1", "5")
    assert result.exit_code == 0, result.output
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert (target / "-1000000000001_5.jpg").is_file()


@pytest.mark.parametrize("below", ["", "sub"])
def test_download_rejects_a_dir_that_cannot_be_created(
    tmp_path: Path, below: str
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    target = os.path.join(blocker, below) if below else str(blocker)
    result, make_client = _invoke_without_client("--dir", target, "1", "5")
    error = _usage_error(result)
    assert target in error
    make_client.assert_not_called()
    assert blocker.read_text() == "not a directory"


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param(0o500, id="read-only"),
        pytest.param(0o200, id="not-searchable"),
    ],
)
def test_download_rejects_a_dir_it_cannot_write_into(
    tmp_path: Path, mode: int
) -> None:
    target = tmp_path / "locked"
    target.mkdir()
    target.chmod(mode)
    with _unlocked_afterwards(target):
        result, make_client = _invoke_without_client(
            "--dir", str(target), "1", "5"
        )
    error = _usage_error(result)
    assert str(target) in error
    make_client.assert_not_called()


def test_download_without_dir_is_a_usage_error() -> None:
    result, make_client = _invoke_without_client("1", "5")
    error = _usage_error(result)
    assert "--dir" in error
    make_client.assert_not_called()


def test_download_without_ids_is_a_usage_error(tmp_path: Path) -> None:
    result, make_client = _invoke_without_client("--dir", str(tmp_path), "1")
    error = _usage_error(result)
    assert "MESSAGE_IDS" in error
    make_client.assert_not_called()


@pytest.mark.parametrize("bad_id", ["0", "abc", "2147483648"])
def test_download_rejects_an_invalid_id(tmp_path: Path, bad_id: str) -> None:
    result, make_client = _invoke_without_client(
        "--dir", str(tmp_path), "1", "5", bad_id
    )
    error = _usage_error(result)
    assert "MESSAGE_IDS" in error
    assert bad_id in error
    make_client.assert_not_called()


@pytest.mark.parametrize("argv", [["1", "-5"], ["--", "1", "-5"]])
def test_download_rejects_a_negative_id(
    tmp_path: Path, argv: list[str]
) -> None:
    result, make_client = _invoke_without_client(
        "--dir", str(tmp_path), *argv
    )
    assert "-5" in _usage_error(result)
    make_client.assert_not_called()


@pytest.mark.parametrize("bad_limit", ["0", "-1", "big"])
def test_download_rejects_an_invalid_max_bytes(
    tmp_path: Path, bad_limit: str
) -> None:
    result, make_client = _invoke_without_client(
        "--dir", str(tmp_path), "--max-bytes", bad_limit, "1", "5"
    )
    error = _usage_error(result)
    assert "--max-bytes" in error
    make_client.assert_not_called()


# --- errors --------------------------------------------------------------


def test_download_errors_when_not_authorized(tmp_path: Path) -> None:
    client = _fake_client(
        entity=_entity(1), stored=[_photo_msg(5)], authorized=False
    )
    result = _invoke(client, "--dir", str(tmp_path), "1", "5")
    assert result.exit_code == 1
    payload = json.loads(result.stderr)
    assert payload["type"] == "AuthError"
    assert "tg login" in payload["error"]
    client.get_messages.assert_not_called()
    client.download_media.assert_not_called()
    client.disconnect.assert_awaited_once()


def test_download_unknown_group_reports_group_not_found(
    tmp_path: Path,
) -> None:
    client = _fake_client(entity=_entity(1), stored=[_photo_msg(5)])
    client.get_entity = AsyncMock(
        side_effect=ValueError("Cannot find any entity")
    )
    result = _invoke(client, "--dir", str(tmp_path), "1", "5")
    assert result.exit_code == 1
    payload = json.loads(result.stderr)
    assert payload["type"] == "GroupNotFoundError"
    client.download_media.assert_not_called()
    client.disconnect.assert_awaited_once()


# --- write path ----------------------------------------------------------


class _ServerError(RPCError):
    def __init__(self) -> None:
        super().__init__(request=None, message="INTERNAL")


def _failing_on(message_id: int, exc: BaseException) -> AsyncMock:
    """``download_media`` that leaves a partial file and raises for one
    message, and saves every other message whole."""

    async def download_media(message: Any, *, file: str, thumb: Any) -> str:
        if message.id == message_id:
            Path(file).write_bytes(b"partial")
            raise exc
        Path(file).write_bytes(b"whole")
        return file

    return AsyncMock(side_effect=download_media)


def test_download_writes_to_a_temporary_name_inside_the_dir(
    tmp_path: Path,
) -> None:
    client = _fake_client(entity=_entity(1), stored=[_photo_msg(5)])
    result = _invoke(client, "--dir", str(tmp_path), "1", "5")
    [entry] = _entries(result)
    [file] = _files(client)
    assert os.path.dirname(file) == str(tmp_path)
    assert file != entry["path"]
    # A name with an extension is one Telethon has no reason to change.
    assert file.endswith(".jpg")
    # Hidden, so a caller listing the photos never picks up a partial one.
    assert os.path.basename(file).startswith(".")
    assert _names(tmp_path) == ["-1000000000001_5.jpg"]


def test_download_through_telethon_fills_the_temporary_file_it_is_given(
    tmp_path: Path,
) -> None:
    """The write path relies on Telethon writing a photo to the existing
    file it is handed and to no other; this runs its real download."""
    message = _real_photo_msg(5, *_placeholders(), _cached("c", 300))
    client = _fake_client(entity=_entity(1), stored=[message])
    client.download_media, returned = _telethon_download()
    result = _invoke(client, "--dir", str(tmp_path), "1", "5")
    [entry] = _entries(result)
    assert returned == _files(client)
    assert (entry["status"], entry["bytes"]) == ("saved", 300)
    assert Path(entry["path"]).read_bytes() == b"c" * 300
    assert _names(tmp_path) == ["-1000000000001_5.jpg"]


def test_download_closes_the_descriptor_of_its_temporary_file(
    tmp_path: Path,
) -> None:
    descriptors = []
    real_mkstemp = tempfile.mkstemp

    def mkstemp(**kwargs: Any) -> tuple[int, str]:
        fd, path = real_mkstemp(**kwargs)
        descriptors.append(fd)
        return fd, path

    client = _fake_client(entity=_entity(1), stored=[_photo_msg(5)])
    with patch(
        "tg_cli.commands.download.tempfile.mkstemp", side_effect=mkstemp
    ):
        result = _invoke(client, "--dir", str(tmp_path), "1", "5")
    assert result.exit_code == 0, result.output
    [fd] = descriptors
    with pytest.raises(OSError):
        os.fstat(fd)


def test_download_that_returns_nothing_is_a_download_error(
    tmp_path: Path,
) -> None:
    client = _fake_client(entity=_entity(1), stored=[_photo_msg(5)])
    client.download_media = AsyncMock(return_value=None)
    result = _invoke(client, "--dir", str(tmp_path), "1", "5")
    payload = _error(result)
    assert payload["type"] == "DownloadError"
    assert "message 5" in payload["error"]
    assert _names(tmp_path) == []
    client.disconnect.assert_awaited_once()


def test_download_that_leaves_an_empty_file_is_a_download_error(
    tmp_path: Path,
) -> None:
    client = _fake_client(
        entity=_entity(1), stored=[_photo_msg(5)], payloads={5: b""}
    )
    result = _invoke(client, "--dir", str(tmp_path), "1", "5")
    payload = _error(result)
    assert payload["type"] == "DownloadError"
    assert "message 5" in payload["error"]
    assert _names(tmp_path) == []


def test_download_deletes_a_file_larger_than_the_variant_declared(
    tmp_path: Path,
) -> None:
    client = _fake_client(
        entity=_entity(1),
        stored=[_photo_msg(5, _size("x", 1000)), _photo_msg(6)],
        payloads={5: b"p" * 2000, 6: b"p" * 2001},
    )
    result = _invoke(
        client, "--dir", str(tmp_path), "--max-bytes", "2000", "1", "5", "6"
    )
    at_limit, over_limit = _entries(result)
    assert (at_limit["status"], at_limit["bytes"]) == ("saved", 2000)
    assert over_limit == _skipped(6, "too_large")
    assert client.download_media.await_count == 2
    assert _names(tmp_path) == ["-1000000000001_5.jpg"]


@pytest.mark.parametrize(
    "exc,error_type,cause",
    [
        pytest.param(
            OSError(errno.ENOSPC, "No space left on device"),
            "DownloadError",
            "No space left on device",
            id="disk",
        ),
        pytest.param(
            ConnectionError("Connection lost"),
            "DownloadError",
            "Connection lost",
            id="connection",
        ),
        # A timeout carries no text of its own.
        pytest.param(
            TimeoutError(), "DownloadError", "TimeoutError", id="timeout"
        ),
        pytest.param(_ServerError(), "TelegramError", "INTERNAL", id="rpc"),
    ],
)
def test_download_failing_midway_leaves_no_partial_file(
    tmp_path: Path, exc: BaseException, error_type: str, cause: str
) -> None:
    client = _fake_client(
        entity=_entity(1), stored=[_photo_msg(5), _photo_msg(6)]
    )
    client.download_media = _failing_on(6, exc)
    result = _invoke(client, "--dir", str(tmp_path), "1", "5", "6")
    payload = _error(result)
    assert payload["type"] == error_type
    assert cause in payload["error"]
    assert not (tmp_path / "-1000000000001_6.jpg").exists()
    assert _names(tmp_path) == ["-1000000000001_5.jpg"]
    assert (tmp_path / "-1000000000001_5.jpg").read_bytes() == b"whole"
    client.disconnect.assert_awaited_once()


def test_download_interrupted_midway_leaves_no_partial_file(
    tmp_path: Path,
) -> None:
    client = _fake_client(
        entity=_entity(1), stored=[_photo_msg(5), _photo_msg(6)]
    )
    client.download_media = _failing_on(6, KeyboardInterrupt())
    result = _invoke(client, "--dir", str(tmp_path), "1", "5", "6")
    assert result.exit_code != 0
    assert result.stdout == ""
    assert _names(tmp_path) == ["-1000000000001_5.jpg"]


def test_download_that_cannot_take_the_target_name_is_a_download_error(
    tmp_path: Path,
) -> None:
    occupied = tmp_path / "-1000000000001_5.jpg"
    occupied.mkdir()
    (occupied / "keep").write_text("kept")
    client = _fake_client(entity=_entity(1), stored=[_photo_msg(5)])
    result = _invoke(client, "--dir", str(tmp_path), "1", "5")
    payload = _error(result)
    assert payload["type"] == "DownloadError"
    assert "message 5" in payload["error"]
    assert _names(tmp_path) == [occupied.name]
    assert (occupied / "keep").read_text() == "kept"


def test_download_that_cannot_create_its_temporary_file_is_a_download_error(
    tmp_path: Path,
) -> None:
    client = _fake_client(entity=_entity(1), stored=[_photo_msg(5)])
    answer = client.get_messages.side_effect

    # The directory passes the start-up check and turns read-only later.
    async def get_messages(entity: Any, *, ids: list[int]) -> list[Any]:
        tmp_path.chmod(0o500)
        return await answer(entity, ids=ids)

    client.get_messages = AsyncMock(side_effect=get_messages)
    with _unlocked_afterwards(tmp_path):
        result = _invoke(client, "--dir", str(tmp_path), "1", "5")
    payload = _error(result)
    assert payload["type"] == "DownloadError"
    assert "message 5" in payload["error"]
    client.download_media.assert_not_called()
    assert _names(tmp_path) == []


def test_download_that_cannot_delete_an_oversize_file_is_a_download_error(
    tmp_path: Path,
) -> None:
    # The directory turns read-only once the file is written.
    async def download_media(message: Any, *, file: str, thumb: Any) -> str:
        Path(file).write_bytes(b"p" * 2001)
        tmp_path.chmod(0o500)
        return file

    client = _fake_client(entity=_entity(1), stored=[_photo_msg(5)])
    client.download_media = AsyncMock(side_effect=download_media)
    with _unlocked_afterwards(tmp_path):
        result = _invoke(
            client, "--dir", str(tmp_path), "--max-bytes", "2000", "1", "5"
        )
    payload = _error(result)
    assert payload["type"] == "DownloadError"
    assert "message 5" in payload["error"]
    [leftover] = _names(tmp_path)
    assert leftover in payload["error"]
    client.disconnect.assert_awaited_once()


@pytest.mark.parametrize(
    "exc,error_type,cause",
    [
        pytest.param(
            ConnectionError("Connection lost"),
            "DownloadError",
            "Connection lost",
            id="connection",
        ),
        pytest.param(_ServerError(), "TelegramError", "INTERNAL", id="rpc"),
        pytest.param(
            FloodWaitError(request=None, capture=30),
            "FloodWaitError",
            "retry after 30 seconds",
            id="flood",
        ),
    ],
)
def test_download_failure_is_still_reported_when_its_cleanup_fails_too(
    tmp_path: Path, exc: BaseException, error_type: str, cause: str
) -> None:
    # The directory turns read-only once the partial file is written.
    async def download_media(message: Any, *, file: str, thumb: Any) -> str:
        Path(file).write_bytes(b"partial")
        tmp_path.chmod(0o500)
        raise exc

    client = _fake_client(entity=_entity(1), stored=[_photo_msg(5)])
    client.download_media = AsyncMock(side_effect=download_media)
    with _unlocked_afterwards(tmp_path):
        result = _invoke(client, "--dir", str(tmp_path), "1", "5")
    payload = _error(result)
    assert payload["type"] == error_type
    assert cause in payload["error"]
    assert "Permission denied" not in payload["error"]
    [leftover] = _names(tmp_path)
    assert leftover.startswith(".")
    client.disconnect.assert_awaited_once()


# --- target name ---------------------------------------------------------


def test_download_replaces_a_symlink_at_the_target_name(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"untouched")
    directory = tmp_path / "photos"
    directory.mkdir()
    target = directory / "-1000000000001_5.jpg"
    target.symlink_to(outside)
    client = _fake_client(
        entity=_entity(1), stored=[_photo_msg(5)], payloads={5: b"photo"}
    )
    result = _invoke(client, "--dir", str(directory), "1", "5")
    [entry] = _entries(result)
    assert entry["status"] == "saved"
    assert not target.is_symlink()
    assert target.read_bytes() == b"photo"
    assert outside.read_bytes() == b"untouched"
    assert _names(directory) == [target.name]


def test_download_overwrites_an_existing_file_only_once_complete(
    tmp_path: Path,
) -> None:
    target = tmp_path / "-1000000000001_5.jpg"
    target.write_bytes(b"old")
    during_download = []

    async def download_media(message: Any, *, file: str, thumb: Any) -> str:
        Path(file).write_bytes(b"new")
        during_download.append(target.read_bytes())
        return file

    client = _fake_client(entity=_entity(1), stored=[_photo_msg(5)])
    client.download_media = AsyncMock(side_effect=download_media)
    result = _invoke(client, "--dir", str(tmp_path), "1", "5")
    [entry] = _entries(result)
    assert entry["status"] == "saved"
    assert during_download == [b"old"]
    assert target.read_bytes() == b"new"
    assert _names(tmp_path) == [target.name]
