"""Acceptance verification.

These tests assert the high-level guarantees promised in the plan
overviews: every CLI command is wired, every output command emits
parseable JSON, ``--pretty`` works on every command that has it, and
every message carries the documented keys.

They are intentionally redundant with per-command tests — their value
is that they exercise the public CLI surface as a single contract,
making any regression in command registration or JSON shape obvious.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner
from telethon.tl import types

from tg_cli import cli


class _AsyncIter:
    def __init__(self, items: Iterable[Any]) -> None:
        self._items = list(items)

    def __aiter__(self) -> _AsyncIter:
        return self

    async def __anext__(self) -> Any:
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


def _entity(id: int = 1, title: str = "Group") -> SimpleNamespace:
    return SimpleNamespace(id=id, title=title)


def _supergroup_dialog() -> SimpleNamespace:
    entity = SimpleNamespace(
        id=-1001,
        title="Dev",
        megagroup=True,
        broadcast=False,
        username="dev",
        participants_count=10,
    )
    return SimpleNamespace(entity=entity, id=-1001, name="Dev")


def _sample_msg(id: int = 1, media: Any = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        message="hi",
        text="hi",
        date=datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc),
        sender=SimpleNamespace(
            first_name="Alice", last_name=None, username="alice"
        ),
        sender_id=42,
        reply_to=None,
        media=media,
    )


def _photo_media() -> types.MessageMediaPhoto:
    photo = types.Photo(
        id=1,
        access_hash=2,
        file_reference=b"",
        date=None,
        sizes=[types.PhotoSize(type="x", w=100, h=100, size=1000)],
        dc_id=1,
    )
    return types.MessageMediaPhoto(photo=photo)


async def _write_photo(_message: Any, *, file: str, thumb: str) -> str:
    Path(file).write_bytes(b"jpeg")
    return file


def _fake_client_for(
    command: str, stored: Iterable[SimpleNamespace] | None = None
) -> MagicMock:
    """Build a mock TelegramClient pre-loaded for a given subcommand.

    ``stored`` is the group's message history, one sample message when
    not given.
    """
    stored = [_sample_msg()] if stored is None else list(stored)
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=True)
    client.get_entity = AsyncMock(return_value=_entity())
    if command in ("get", "download"):
        # The usual answer for a list of ids: request order, ``None``
        # in place of each id that does not exist.
        by_id = {m.id: m for m in stored}

        async def get_messages(_entity: Any, *, ids: list[int]) -> list[Any]:
            return [by_id.get(i) for i in ids]

        client.get_messages = AsyncMock(side_effect=get_messages)
    else:
        client.get_messages = AsyncMock(return_value=stored[0])
    client.download_media = AsyncMock(side_effect=_write_photo)
    if command == "groups":
        client.iter_dialogs = MagicMock(
            return_value=_AsyncIter([_supergroup_dialog()])
        )
    else:
        client.iter_dialogs = MagicMock(return_value=_AsyncIter([]))
    client.iter_messages = MagicMock(return_value=_AsyncIter(stored))
    return client


# Argv after the subcommand name for each output command. Kept short so
# the parametrised test reads as a contract spec, not a fixture dump.
_OUTPUT_COMMANDS: list[tuple[str, list[str]]] = [
    ("groups", []),
    ("messages", ["1"]),
    ("search", ["1", "query"]),
    ("thread", ["1", "1"]),
    ("get", ["1", "1"]),
]

_COMMANDS = {
    "login",
    "groups",
    "messages",
    "search",
    "thread",
    "get",
    "download",
}

_MESSAGE_KEYS = [
    "id",
    "date",
    "sender_id",
    "sender_name",
    "text",
    "reply_to_id",
    "group_id",
    "sender_username",
    "topic_id",
    "media_kind",
    "grouped_id",
    "urls",
    "forward",
    "link",
]


def test_cli_exposes_all_overview_commands() -> None:
    """Every command from the plan overviews is registered."""
    assert _COMMANDS.issubset(set(cli.main.commands))


def test_help_lists_every_command() -> None:
    result = CliRunner().invoke(cli.main, ["--help"])
    assert result.exit_code == 0, result.output
    command_lines = result.stdout.split("Commands:")[1].splitlines()
    listed = {line.split()[0] for line in command_lines if line.strip()}
    assert _COMMANDS.issubset(listed)


def test_help_shows_the_whole_summary_of_every_command() -> None:
    result = CliRunner().invoke(cli.main, ["--help"])
    assert result.exit_code == 0, result.output
    command_lines = result.stdout.split("Commands:\n")[1].splitlines()
    # Click ends a summary it had to cut with "...".
    assert [line for line in command_lines if line.endswith("...")] == []


@pytest.mark.parametrize("command,extra_args", _OUTPUT_COMMANDS)
def test_command_default_output_is_parseable_json_array(
    command: str, extra_args: list[str]
) -> None:
    client = _fake_client_for(command)
    target = f"tg_cli.commands.{command}.make_client"
    with patch(target, return_value=client):
        result = CliRunner().invoke(cli.main, [command, *extra_args])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert data, "expected at least one mocked record in output"


@pytest.mark.parametrize("command,extra_args", _OUTPUT_COMMANDS)
def test_command_pretty_output_is_parseable_json_array(
    command: str, extra_args: list[str]
) -> None:
    client = _fake_client_for(command)
    target = f"tg_cli.commands.{command}.make_client"
    with patch(target, return_value=client):
        result = CliRunner().invoke(
            cli.main, [command, *extra_args, "--pretty"]
        )
    assert result.exit_code == 0, result.output
    # Pretty output is indented; default is not.
    assert "\n  " in result.stdout
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert data


@pytest.mark.parametrize(
    "command,extra_args",
    [c for c in _OUTPUT_COMMANDS if c[0] != "groups"],
)
def test_command_emits_messages_with_the_documented_keys(
    command: str, extra_args: list[str]
) -> None:
    client = _fake_client_for(command)
    target = f"tg_cli.commands.{command}.make_client"
    with patch(target, return_value=client):
        result = CliRunner().invoke(cli.main, [command, *extra_args])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data, "expected at least one mocked message in output"
    for message in data:
        assert list(message) == _MESSAGE_KEYS


def test_range_paging_reads_every_message_once() -> None:
    """The paging contract: move the cursor to the last id of each full
    page and stop at the first short one."""
    stored = [_sample_msg(i) for i in (3, 4, 7, 8, 12)]

    def iter_messages(
        _entity: Any,
        *,
        limit: int,
        min_id: int = 0,
        max_id: int = 0,
        reverse: bool = False,
    ) -> _AsyncIter:
        # Telethon's id bounds: both exclusive, ``max_id=0`` is no bound.
        selected = [
            m
            for m in stored
            if m.id > min_id and (max_id == 0 or m.id < max_id)
        ]
        if not reverse:
            selected.reverse()
        return _AsyncIter(selected[:limit])

    client = _fake_client_for("messages")
    client.iter_messages = MagicMock(side_effect=iter_messages)
    cursor, newest = 2, 12
    pages = []
    with patch("tg_cli.commands.messages.make_client", return_value=client):
        for _ in range(3):
            result = CliRunner().invoke(
                cli.main,
                [
                    "messages",
                    "1",
                    "--after-id",
                    str(cursor),
                    "--through-id",
                    str(newest),
                    "--limit",
                    "2",
                ],
            )
            assert result.exit_code == 0, result.output
            page = [m["id"] for m in json.loads(result.stdout)]
            pages.append(page)
            cursor = page[-1]
    assert pages == [[3, 4], [7, 8], [12]]


def test_download_reports_one_entry_per_requested_id(tmp_path: Path) -> None:
    client = _fake_client_for(
        "download", [_sample_msg(1, _photo_media()), _sample_msg(2)]
    )
    with patch("tg_cli.commands.download.make_client", return_value=client):
        result = CliRunner().invoke(
            cli.main, ["download", "--dir", str(tmp_path), "1", "1", "2", "3"]
        )
    assert result.exit_code == 0, result.output
    entries = json.loads(result.stdout)
    for entry in entries:
        assert list(entry) == ["id", "status", "path", "bytes", "reason"]
    assert [(e["id"], e["status"], e["reason"]) for e in entries] == [
        (1, "saved", None),
        (2, "skipped", "not_photo"),
        (3, "skipped", "not_found"),
    ]
    assert Path(entries[0]["path"]).read_bytes() == b"jpeg"


# --- Migrated-basic-chat end-to-end contract ----------------------------

# Distinct identifiers chosen so they do not collide with any other
# fixture in the test suite: 730811422 is the bare old-chat id (its
# marked form is -730811422); 881744120 is the bare new-channel id
# (marked form -100881744120).
_MIGRATED_OLD_CHAT_ID = 730811422
_MIGRATED_CHANNEL_ID = 881744120
_MIGRATED_CHANNEL_MARKED_ID = -1_000_000_000_000 - _MIGRATED_CHANNEL_ID


def _fake_migrated_client() -> MagicMock:
    """Client whose dialog list carries a migrated zombie + its target.

    ``get_entity`` is driven by ``side_effect``: first call receives the
    raw ``-<old_chat_id>`` integer (Telethon semantics), second call
    receives the ``migrated_to`` pointer from the first result, so both
    sequential lookups behind the migration follow-through are covered.
    """
    migrated_pointer = SimpleNamespace(
        channel_id=_MIGRATED_CHANNEL_ID, access_hash=7
    )
    migrated = SimpleNamespace(
        id=_MIGRATED_OLD_CHAT_ID,
        title="Legacy Team",
        participants_count=0,
        username=None,
        migrated_to=migrated_pointer,
    )
    channel = SimpleNamespace(
        id=_MIGRATED_CHANNEL_ID,
        title="Legacy Team (migrated)",
        megagroup=True,
        broadcast=False,
        username="legacyteam",
        participants_count=25,
        migrated_to=None,
    )
    post_migration_msg = SimpleNamespace(
        id=77,
        message="after migration",
        text="after migration",
        date=datetime(2026, 4, 18, 9, 30, tzinfo=timezone.utc),
        sender=SimpleNamespace(
            first_name="Bob", last_name=None, username="bob"
        ),
        sender_id=99,
        reply_to=None,
    )

    def _wrap(entity: SimpleNamespace) -> SimpleNamespace:
        return SimpleNamespace(entity=entity, id=entity.id, name=entity.title)

    async def _get_entity(key: Any) -> Any:
        if key == -_MIGRATED_OLD_CHAT_ID:
            return migrated
        if key is migrated.migrated_to:
            return channel
        raise AssertionError(f"unexpected get_entity key: {key!r}")

    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=True)
    client.get_entity = AsyncMock(side_effect=_get_entity)
    client.iter_dialogs = MagicMock(
        return_value=_AsyncIter([_wrap(migrated), _wrap(channel)])
    )
    client.iter_messages = MagicMock(
        return_value=_AsyncIter([post_migration_msg])
    )
    # Stored as attribute so tests that need it (e.g. ``tg thread``'s
    # root lookup) can reuse the same message object.
    client._post_migration_msg = post_migration_msg
    return client


def test_migrated_chat_filtered_from_groups_listing() -> None:
    """A migrated basic chat disappears from the ``tg groups`` listing,
    and the replacement supergroup shows up under its marked ``-100…`` id."""
    client = _fake_migrated_client()

    with patch(
        "tg_cli.commands.groups.make_client", return_value=client
    ):
        listing = CliRunner().invoke(
            cli.main, ["groups", "--type", "group"]
        )
    assert listing.exit_code == 0, listing.output
    groups_payload = json.loads(listing.stdout)
    ids = [g["id"] for g in groups_payload]
    titles = [g["title"] for g in groups_payload]
    # Zombie basic-chat id (``-<bare>``) must be gone.
    assert -_MIGRATED_OLD_CHAT_ID not in ids
    # Migrated-to supergroup must still show up under its marked id.
    assert _MIGRATED_CHANNEL_MARKED_ID in ids
    assert "Legacy Team (migrated)" in titles


def test_migrated_chat_redirects_tg_messages() -> None:
    """Old basic-chat id passed to ``tg messages`` transparently redirects
    to the new supergroup. ``group_id`` on returned messages must be the
    supergroup's marked ``-100…`` id, NOT the id on the command line —
    this is the JSON-semantics change documented under "JSON semantics
    change (intentional)" in the plan."""
    client = _fake_migrated_client()
    with patch(
        "tg_cli.commands.messages.make_client", return_value=client
    ):
        msgs = CliRunner().invoke(
            cli.main, ["messages", "--", f"-{_MIGRATED_OLD_CHAT_ID}"]
        )
    assert msgs.exit_code == 0, msgs.output
    messages_payload = json.loads(msgs.stdout)
    assert len(messages_payload) == 1
    assert messages_payload[0]["group_id"] == _MIGRATED_CHANNEL_MARKED_ID
    # Sanity: the old id is NEVER echoed back as ``group_id``.
    assert messages_payload[0]["group_id"] != -_MIGRATED_OLD_CHAT_ID
    # ``iter_messages`` must have been called against the resolved
    # supergroup entity, not the zombie chat.
    (entity_arg,), _ = client.iter_messages.call_args
    assert entity_arg.id == _MIGRATED_CHANNEL_ID


def test_migrated_chat_redirects_tg_search() -> None:
    """``tg search`` shares the same resolver as ``tg messages`` but has
    its own CLI wiring. Verify end-to-end that the old basic-chat id
    redirects and ``group_id`` reports the supergroup's marked id."""
    client = _fake_migrated_client()
    with patch(
        "tg_cli.commands.search.make_client", return_value=client
    ):
        res = CliRunner().invoke(
            cli.main, ["search", "--", f"-{_MIGRATED_OLD_CHAT_ID}", "after"]
        )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert len(payload) == 1
    assert payload[0]["group_id"] == _MIGRATED_CHANNEL_MARKED_ID
    (entity_arg,), _ = client.iter_messages.call_args
    assert entity_arg.id == _MIGRATED_CHANNEL_ID


def test_migrated_chat_redirects_tg_thread() -> None:
    """``tg thread`` also shares the resolver. Root message's ``group_id``
    must be the supergroup's marked id."""
    client = _fake_migrated_client()
    # ``tg thread`` calls ``get_messages`` for the root before iterating
    # replies; the shared fake doesn't define it, so patch locally.
    client.get_messages = AsyncMock(return_value=client._post_migration_msg)
    with patch(
        "tg_cli.commands.thread.make_client", return_value=client
    ):
        res = CliRunner().invoke(
            cli.main, ["thread", "--", f"-{_MIGRATED_OLD_CHAT_ID}", "77"]
        )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload, "expected at least the root message"
    assert payload[0]["group_id"] == _MIGRATED_CHANNEL_MARKED_ID
    (root_entity,), _ = client.get_messages.call_args
    assert root_entity.id == _MIGRATED_CHANNEL_ID


def test_migrated_chat_redirects_tg_get() -> None:
    """``tg get`` shares the resolver too: the message is fetched from
    the supergroup and carries its marked id."""
    client = _fake_migrated_client()
    client.get_messages = AsyncMock(return_value=[client._post_migration_msg])
    with patch("tg_cli.commands.get.make_client", return_value=client):
        res = CliRunner().invoke(
            cli.main, ["get", "--", f"-{_MIGRATED_OLD_CHAT_ID}", "77"]
        )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert [m["group_id"] for m in payload] == [_MIGRATED_CHANNEL_MARKED_ID]
    (entity_arg,), _ = client.get_messages.call_args
    assert entity_arg.id == _MIGRATED_CHANNEL_ID


def test_migrated_chat_redirects_tg_download(tmp_path: Path) -> None:
    """``tg download`` names the saved file by the supergroup's marked
    id, not by the old chat id given on the command line."""
    client = _fake_migrated_client()
    client._post_migration_msg.media = _photo_media()
    client.get_messages = AsyncMock(return_value=[client._post_migration_msg])
    client.download_media = AsyncMock(side_effect=_write_photo)
    with patch("tg_cli.commands.download.make_client", return_value=client):
        res = CliRunner().invoke(
            cli.main,
            [
                "download",
                "--dir",
                str(tmp_path),
                "--",
                f"-{_MIGRATED_OLD_CHAT_ID}",
                "77",
            ],
        )
    assert res.exit_code == 0, res.output
    [entry] = json.loads(res.stdout)
    saved = tmp_path / f"{_MIGRATED_CHANNEL_MARKED_ID}_77.jpg"
    assert (entry["status"], entry["path"]) == ("saved", str(saved))
    assert [p.name for p in tmp_path.iterdir()] == [saved.name]
    (entity_arg,), _ = client.get_messages.call_args
    assert entity_arg.id == _MIGRATED_CHANNEL_ID
