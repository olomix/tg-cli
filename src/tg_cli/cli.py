"""Top-level Click command group for ``tg``.

Subcommands are registered here. Each subcommand module exports a single
``click.Command`` (or group) object that gets attached to ``main``.
"""

import click

from .commands.login import login


@click.group(help="Read your Telegram groups via MTProto (Telethon).")
@click.version_option(package_name="tg-cli")
def main() -> None:
    """Entry point referenced by the ``tg`` console script."""


main.add_command(login)


if __name__ == "__main__":
    main()
