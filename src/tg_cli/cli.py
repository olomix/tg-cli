"""Top-level Click command group for ``tg``.

Subcommands are registered here. Each subcommand module exports a single
``click.Command`` (or group) object that gets attached to ``main``.
"""

from __future__ import annotations

import sys
from typing import Any

import click

from .commands.groups import groups
from .commands.login import login
from .commands.messages import messages
from .commands.search import search
from .commands.thread import thread
from .errors import emit_error


class _JsonErrorGroup(click.Group):
    """Click ``Group`` that emits parse/usage failures as the JSON error
    contract documented in ``README.md`` and ``skill/SKILL.md``.

    Click's default behavior prints plain-text ``Usage: ...\\nError: ...``
    to stderr on missing arguments, unknown options, and type-cast
    failures. Consumers of ``tg`` (Claude and shell pipelines) parse
    stderr as JSON on every failure path, so we intercept
    :class:`click.UsageError` (and other :class:`click.ClickException`
    subclasses) before Click formats them and reuse
    :func:`emit_error`. Exit code stays at 2 for usage errors — Click's
    convention — so callers can distinguish usage failures (exit 2)
    from domain errors (exit 1).
    """

    def main(self, *args: Any, **kwargs: Any) -> Any:
        # Force non-standalone parsing so Click raises exceptions to us
        # instead of printing its plain-text message directly.
        kwargs["standalone_mode"] = False
        try:
            rv = super().main(*args, **kwargs)
        except click.UsageError as exc:
            emit_error(exc.format_message(), "UsageError")
            sys.exit(exc.exit_code)
        except click.ClickException as exc:
            emit_error(exc.format_message(), type(exc).__name__)
            sys.exit(exc.exit_code)
        except click.exceptions.Abort:
            emit_error("aborted", "Abort")
            sys.exit(1)
        # ``click.exceptions.Exit`` raised by a callback (e.g. from the
        # ``handle_errors`` decorator) is converted by Click to an int
        # return value in non-standalone mode. Re-raise as ``SystemExit``
        # so both ``CliRunner`` and the console entry point observe the
        # non-zero exit status.
        if isinstance(rv, int) and rv != 0:
            sys.exit(rv)
        return rv


@click.group(
    cls=_JsonErrorGroup,
    help="Read your Telegram groups via MTProto (Telethon).",
)
@click.version_option(package_name="tg-cli")
def main() -> None:
    """Entry point referenced by the ``tg`` console script."""


main.add_command(login)
main.add_command(groups)
main.add_command(messages)
main.add_command(search)
main.add_command(thread)


if __name__ == "__main__":
    main()
