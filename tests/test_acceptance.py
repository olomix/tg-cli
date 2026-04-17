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
