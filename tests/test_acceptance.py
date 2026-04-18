"""Task 9 acceptance verification.

These tests assert the high-level guarantees promised in the plan
overview: the five CLI commands are wired, every output command emits
parseable JSON, and ``--pretty`` works on every output command.

They are intentionally redundant with per-command tests — their value
is that they exercise the public CLI surface as a single contract,
making any regression in command registration or JSON shape obvious.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

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


def _sample_msg() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        message="hi",
        text="hi",
        date=datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc),
        sender=SimpleNamespace(
            first_name="Alice", last_name=None, username="alice"
        ),
        sender_id=42,
        reply_to=None,
    )


def _fake_client_for(command: str) -> MagicMock:
    """Build a mock TelegramClient pre-loaded for a given subcommand."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=True)
    client.get_entity = AsyncMock(return_value=_entity())
    client.get_messages = AsyncMock(return_value=_sample_msg())
    if command == "groups":
        client.iter_dialogs = MagicMock(
            return_value=_AsyncIter([_supergroup_dialog()])
        )
    else:
        client.iter_dialogs = MagicMock(return_value=_AsyncIter([]))
    client.iter_messages = MagicMock(
        return_value=_AsyncIter([_sample_msg()])
    )
    return client


# Argv after the subcommand name for each output command. Kept short so
# the parametrised test reads as a contract spec, not a fixture dump.
_OUTPUT_COMMANDS: list[tuple[str, list[str]]] = [
    ("groups", []),
    ("messages", ["1"]),
    ("search", ["1", "query"]),
    ("thread", ["1", "1"]),
]


def test_cli_exposes_all_overview_commands() -> None:
    """All five commands from the Overview are registered."""
    expected = {"login", "groups", "messages", "search", "thread"}
    assert expected.issubset(set(cli.main.commands))


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


# --- Migrated-basic-chat end-to-end contract ----------------------------

# Distinct identifiers chosen so they do not collide with any other
# fixture in the test suite: 730811422 is the bare old-chat id (its
# marked form is -730811422); 881744120 is the bare new-channel id
# (marked form -100881744120).
_MIGRATED_OLD_CHAT_ID = 730811422
_MIGRATED_CHANNEL_ID = 881744120
_MIGRATED_CHANNEL_MARKED_ID = -1_000_000_000_000 - _MIGRATED_CHANNEL_ID


def _migrated_chat_entity() -> SimpleNamespace:
    """Basic ``Chat`` double whose ``migrated_to`` points at a channel."""
    pointer = SimpleNamespace(
        channel_id=_MIGRATED_CHANNEL_ID, access_hash=7
    )
    return SimpleNamespace(
        id=_MIGRATED_OLD_CHAT_ID,
        title="Legacy Team",
        participants_count=0,
        username=None,
        migrated_to=pointer,
    )


def _migrated_target_channel() -> SimpleNamespace:
    return SimpleNamespace(
        id=_MIGRATED_CHANNEL_ID,
        title="Legacy Team (migrated)",
        megagroup=True,
        broadcast=False,
        username="legacyteam",
        participants_count=25,
        migrated_to=None,
    )


def _dialog_wrap(entity: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        entity=entity,
        id=entity.id,
        name=getattr(entity, "title", None),
    )


def _post_migration_msg() -> SimpleNamespace:
    return SimpleNamespace(
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


def _fake_migrated_client() -> MagicMock:
    """Client whose dialog list carries a migrated zombie + its target.

    ``get_entity`` is driven by ``side_effect``: first call receives the
    raw ``-<old_chat_id>`` integer (Telethon semantics), second call
    receives the ``migrated_to`` pointer from the first result, so both
    sequential lookups behind the migration follow-through are covered.
    """
    migrated = _migrated_chat_entity()
    channel = _migrated_target_channel()

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
        return_value=_AsyncIter(
            [_dialog_wrap(migrated), _dialog_wrap(channel)]
        )
    )
    client.iter_messages = MagicMock(
        return_value=_AsyncIter([_post_migration_msg()])
    )
    return client


def test_migrated_chat_is_filtered_from_groups_and_redirects_messages() -> None:
    """End-to-end contract: a migrated basic chat disappears from the
    ``tg groups`` listing, and its old id transparently redirects to the
    new supergroup in ``tg messages``. The ``group_id`` on returned
    messages must be the supergroup's marked ``-100…`` id, NOT the id
    passed on the command line — this is the JSON-semantics change the
    plan documents under "JSON semantics change (intentional)"."""
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

    # Rebuild the client: the previous invocation already exhausted
    # ``iter_dialogs`` / ``iter_messages`` (single-shot async iters) and
    # ``get_entity.side_effect`` is a callable so it is fine to reuse,
    # but a clean client makes the second assertion independent.
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
    assert (
        messages_payload[0]["group_id"] == _MIGRATED_CHANNEL_MARKED_ID
    )
    # Sanity: the old id is NEVER echoed back as ``group_id``.
    assert messages_payload[0]["group_id"] != -_MIGRATED_OLD_CHAT_ID
    # ``iter_messages`` must have been called against the resolved
    # supergroup entity, not the zombie chat.
    (entity_arg,), _ = client.iter_messages.call_args
    assert entity_arg.id == _MIGRATED_CHANNEL_ID
