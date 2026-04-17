"""Tests for the ``tg groups`` command and :class:`tg_cli.models.Group`."""

from __future__ import annotations

import json
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from tg_cli import cli
from tg_cli.commands.groups import _dialog_to_group, _matches_filter
from tg_cli.config import ConfigError
from tg_cli.models import Group


class _AsyncDialogIter:
    """Minimal async iterator yielding pre-built dialog objects."""

    def __init__(self, items: Iterable[Any]) -> None:
        self._items = list(items)

    def __aiter__(self) -> _AsyncDialogIter:
        return self

    async def __anext__(self) -> Any:
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


def _small_group(
    *, id: int, title: str, members: int | None = None
) -> SimpleNamespace:
    entity = SimpleNamespace(
        id=id, title=title, participants_count=members, username=None
    )
    return SimpleNamespace(entity=entity, id=id, name=title)


def _supergroup(
    *,
    id: int,
    title: str,
    username: str | None = None,
    members: int | None = None,
) -> SimpleNamespace:
    entity = SimpleNamespace(
        id=id,
        title=title,
        megagroup=True,
        broadcast=False,
        username=username,
        participants_count=members,
    )
    return SimpleNamespace(entity=entity, id=id, name=title)


def _channel(
    *,
    id: int,
    title: str,
    username: str | None = None,
    members: int | None = None,
) -> SimpleNamespace:
    entity = SimpleNamespace(
        id=id,
        title=title,
        megagroup=False,
        broadcast=True,
        username=username,
        participants_count=members,
    )
    return SimpleNamespace(entity=entity, id=id, name=title)


def _user_dialog(*, id: int, first_name: str) -> SimpleNamespace:
    entity = SimpleNamespace(id=id, first_name=first_name, username=None)
    return SimpleNamespace(entity=entity, id=id, name=first_name)


def _fake_client(dialogs: Iterable[Any], authorized: bool = True) -> MagicMock:
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=authorized)
    # Telethon's iter_dialogs() is a regular method returning an async
    # iterator; not a coroutine.
    client.iter_dialogs = MagicMock(
        return_value=_AsyncDialogIter(list(dialogs))
    )
    return client


def _run_groups(
    dialogs: Iterable[Any],
    *args: str,
    authorized: bool = True,
) -> tuple[int, str, str, MagicMock]:
    client = _fake_client(dialogs, authorized=authorized)
    with patch(
        "tg_cli.commands.groups.make_client", return_value=client
    ):
        result = CliRunner().invoke(cli.main, ["groups", *args])
    return result.exit_code, result.stdout, result.stderr, client


def test_group_to_dict_has_expected_shape() -> None:
    g = Group(
        id=-100123,
        title="Dev",
        type="supergroup",
        username="dev",
        member_count=42,
    )
    assert g.to_dict() == {
        "id": -100123,
        "title": "Dev",
        "type": "supergroup",
        "username": "dev",
        "member_count": 42,
    }


def test_matches_filter_semantics() -> None:
    # "group" filter includes both small groups and supergroups.
    assert _matches_filter("group", "group") is True
    assert _matches_filter("supergroup", "group") is True
    assert _matches_filter("channel", "group") is False
    # "channel" filter includes broadcast channels only.
    assert _matches_filter("channel", "channel") is True
    assert _matches_filter("supergroup", "channel") is False
    # "all" includes everything.
    for kind in ("group", "supergroup", "channel"):
        assert _matches_filter(kind, "all") is True


def test_dialog_to_group_skips_user_dms() -> None:
    assert _dialog_to_group(_user_dialog(id=7, first_name="Alice")) is None


def test_dialog_to_group_classifies_each_kind() -> None:
    small = _dialog_to_group(
        _small_group(id=1, title="Small", members=3)
    )
    assert small is not None and small.type == "group"

    mega = _dialog_to_group(
        _supergroup(id=2, title="Mega", username="meg", members=1000)
    )
    assert mega is not None and mega.type == "supergroup"
    assert mega.username == "meg"
    assert mega.member_count == 1000

    chan = _dialog_to_group(
        _channel(id=3, title="News", username="news", members=500)
    )
    assert chan is not None and chan.type == "channel"


def test_groups_returns_json_array_with_contract_fields() -> None:
    dialogs = [
        _supergroup(
            id=-1001234567890,
            title="My Dev Group",
            username="mydevgroup",
            members=42,
        ),
    ]
    exit_code, out, _err, _ = _run_groups(dialogs)
    assert exit_code == 0, out
    data = json.loads(out)
    assert data == [
        {
            "id": -1001234567890,
            "title": "My Dev Group",
            "type": "supergroup",
            "username": "mydevgroup",
            "member_count": 42,
        }
    ]


def test_groups_filter_group_excludes_broadcast_channels() -> None:
    dialogs = [
        _small_group(id=1, title="Small"),
        _supergroup(id=2, title="Mega"),
        _channel(id=3, title="News"),
        _user_dialog(id=99, first_name="Bob"),
    ]
    exit_code, out, _err, _ = _run_groups(dialogs, "--type", "group")
    assert exit_code == 0, out
    data = json.loads(out)
    titles = [d["title"] for d in data]
    assert titles == ["Small", "Mega"]


def test_groups_filter_channel_excludes_groups() -> None:
    dialogs = [
        _small_group(id=1, title="Small"),
        _supergroup(id=2, title="Mega"),
        _channel(id=3, title="News"),
    ]
    exit_code, out, _err, _ = _run_groups(dialogs, "--type", "channel")
    assert exit_code == 0, out
    data = json.loads(out)
    assert [d["title"] for d in data] == ["News"]


def test_groups_filter_all_is_default_and_excludes_dms() -> None:
    dialogs = [
        _small_group(id=1, title="Small"),
        _user_dialog(id=99, first_name="Bob"),
        _channel(id=3, title="News"),
    ]
    exit_code, out, _err, _ = _run_groups(dialogs)
    assert exit_code == 0, out
    data = json.loads(out)
    assert [d["title"] for d in data] == ["Small", "News"]


def test_groups_limit_caps_results() -> None:
    dialogs = [
        _supergroup(id=i, title=f"G{i}") for i in range(1, 6)
    ]
    exit_code, out, _err, _ = _run_groups(dialogs, "--limit", "2")
    assert exit_code == 0, out
    data = json.loads(out)
    assert len(data) == 2
    assert [d["title"] for d in data] == ["G1", "G2"]


def test_groups_limit_applies_after_filtering() -> None:
    dialogs = [
        _channel(id=10, title="News"),
        _small_group(id=1, title="Small"),
        _supergroup(id=2, title="Mega"),
        _supergroup(id=3, title="Mega2"),
    ]
    exit_code, out, _err, _ = _run_groups(
        dialogs, "--type", "group", "--limit", "2"
    )
    assert exit_code == 0, out
    data = json.loads(out)
    assert [d["title"] for d in data] == ["Small", "Mega"]


def test_groups_pretty_flag_indents_output() -> None:
    dialogs = [_small_group(id=1, title="Small")]
    exit_code, pretty_out, _err, _ = _run_groups(dialogs, "--pretty")
    assert exit_code == 0, pretty_out
    # Indented JSON contains newlines between entries.
    assert "\n" in pretty_out.rstrip()
    assert json.loads(pretty_out) == [
        {
            "id": 1,
            "title": "Small",
            "type": "group",
            "username": None,
            "member_count": None,
        }
    ]


def test_groups_default_output_is_single_line() -> None:
    dialogs = [
        _small_group(id=1, title="A"),
        _small_group(id=2, title="B"),
    ]
    exit_code, out, _err, _ = _run_groups(dialogs)
    assert exit_code == 0, out
    # Single trailing newline from click.echo, none inside the JSON body.
    assert out.count("\n") == 1


def test_groups_errors_when_not_authorized() -> None:
    exit_code, _out, err, client = _run_groups([], authorized=False)
    assert exit_code != 0
    assert "Not logged in" in err
    assert "tg login" in err
    client.iter_dialogs.assert_not_called()
    client.disconnect.assert_awaited_once()


def test_groups_empty_result_is_empty_array() -> None:
    exit_code, out, _err, _ = _run_groups([])
    assert exit_code == 0, out
    assert json.loads(out) == []


def test_groups_surfaces_config_error() -> None:
    with patch(
        "tg_cli.commands.groups.make_client",
        side_effect=ConfigError("credentials missing; see README"),
    ):
        result = CliRunner().invoke(cli.main, ["groups"])
    assert result.exit_code != 0
    assert "credentials missing" in result.stderr


def test_groups_disconnects_on_success() -> None:
    dialogs = [_small_group(id=1, title="Small")]
    exit_code, _out, _err, client = _run_groups(dialogs)
    assert exit_code == 0
    client.connect.assert_awaited_once()
    client.disconnect.assert_awaited_once()
