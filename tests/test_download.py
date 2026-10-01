"""Tests for the ``tg download`` command."""

from __future__ import annotations

import errno
import json
import os
import stat
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner
from telethon.client.downloads import DownloadMethods
from telethon.errors import RPCError
from telethon.tl import types

from tg_cli import cli

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
    """Client whose ``get_messages`` answers like Telethon does for a
    list of ids, and whose ``download_media`` writes the message's
    payload to the path it is given and returns that path."""
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
    assert list(tmp_path.iterdir()) == []


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
    assert list(tmp_path.iterdir()) == []


def test_download_returns_one_entry_per_id_in_request_order(
    tmp_path: Path,
) -> None:
    entity = _entity(1)
    client = _fake_client(
        entity=entity, stored=[_msg(3), _photo_msg(5), _photo_msg(7)]
    )
    result = _invoke(client, "--dir", str(tmp_path), "1", "7", "3", "9", "5")
    summary = [
        (e["id"], e["status"], e["reason"]) for e in _entries(result)
    ]
    assert summary == [
        (7, "saved", None),
        (3, "skipped", "not_photo"),
        (9, "skipped", "not_found"),
        (5, "saved", None),
    ]
    client.get_messages.assert_awaited_once_with(entity, ids=[7, 3, 9, 5])
    assert sorted(p.name for p in tmp_path.iterdir()) == [
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
    "sizes",
    [
        pytest.param(_placeholders(), id="placeholders-only"),
        pytest.param([], id="no-sizes"),
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
    assert list(tmp_path.iterdir()) == []


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
    assert DownloadMethods._get_thumb(sizes, thumb) is chosen


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


def test_download_rejects_a_read_only_dir(tmp_path: Path) -> None:
    target = tmp_path / "ro"
    target.mkdir()
    target.chmod(0o500)
    try:
        result, make_client = _invoke_without_client(
            "--dir", str(target), "1", "5"
        )
    finally:
        target.chmod(0o700)
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
    # Telethon appends an extension to a name that has none and would
    # then write to a file the command does not know about.
    assert file.endswith(".jpg")
    # Hidden, so a caller listing the photos never picks up a partial one.
    assert os.path.basename(file).startswith(".")
    assert _names(tmp_path) == ["-1000000000001_5.jpg"]


def test_download_saves_the_file_telethon_reports_writing(
    tmp_path: Path,
) -> None:
    async def download_media(message: Any, *, file: str, thumb: Any) -> str:
        renamed = file[: -len(".jpg")] + " (1).jpg"
        Path(renamed).write_bytes(b"photo")
        return renamed

    client = _fake_client(entity=_entity(1), stored=[_photo_msg(5)])
    client.download_media = AsyncMock(side_effect=download_media)
    result = _invoke(client, "--dir", str(tmp_path), "1", "5")
    [entry] = _entries(result)
    assert entry["status"] == "saved"
    assert entry["bytes"] == 5
    assert Path(entry["path"]).read_bytes() == b"photo"
    assert _names(tmp_path) == ["-1000000000001_5.jpg"]


def test_download_deletes_an_oversize_file_telethon_renamed(
    tmp_path: Path,
) -> None:
    async def download_media(message: Any, *, file: str, thumb: Any) -> str:
        renamed = file[: -len(".jpg")] + " (1).jpg"
        Path(renamed).write_bytes(b"p" * 2001)
        return renamed

    client = _fake_client(entity=_entity(1), stored=[_photo_msg(5)])
    client.download_media = AsyncMock(side_effect=download_media)
    result = _invoke(
        client, "--dir", str(tmp_path), "--max-bytes", "2000", "1", "5"
    )
    assert _entries(result) == [_skipped(5, "too_large")]
    assert _names(tmp_path) == []


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
    try:
        result = _invoke(client, "--dir", str(tmp_path), "1", "5")
    finally:
        tmp_path.chmod(0o700)
    payload = _error(result)
    assert payload["type"] == "DownloadError"
    assert "message 5" in payload["error"]
    client.download_media.assert_not_called()
    assert _names(tmp_path) == []


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
