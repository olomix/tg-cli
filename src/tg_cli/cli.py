"""Top-level Click command group.

Subcommands are wired in by later tasks (login, groups, messages,
search, thread). This skeleton exists so the entry point resolves
and tests can confirm it is importable.
"""

import click


@click.group(help="Read your Telegram groups via MTProto (Telethon).")
@click.version_option(package_name="tg-cli")
def main() -> None:
    """Entry point referenced by the `tg` console script."""


if __name__ == "__main__":
    main()
