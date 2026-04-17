"""Top-level Click command group for ``tg``.

Subcommands are registered here. Each subcommand module exports a single
``click.Command`` (or group) object that gets attached to ``main``.
"""

import click

from .commands.groups import groups
from .commands.login import login
from .commands.messages import messages
from .commands.search import search


@click.group(help="Read your Telegram groups via MTProto (Telethon).")
@click.version_option(package_name="tg-cli")
def main() -> None:
    """Entry point referenced by the ``tg`` console script."""


main.add_command(login)
main.add_command(groups)
main.add_command(messages)
main.add_command(search)


if __name__ == "__main__":
    main()
