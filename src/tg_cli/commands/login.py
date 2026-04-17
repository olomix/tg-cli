"""``tg login`` — interactive Telegram authentication."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import click
from telethon.errors import SessionPasswordNeededError

from ..client import make_client
from ..errors import handle_errors


@click.command()
@click.option(
    "--phone",
    default=None,
    help="Phone number in +country format; prompted for when omitted.",
)
@handle_errors
def login(phone: str | None) -> None:
    """Authenticate with Telegram and persist the session file."""
    asyncio.run(_run_login(phone))


async def _run_login(phone: str | None) -> None:
    client = make_client()
    authenticated = False
    await client.connect()
    try:
        if await client.is_user_authorized():
            click.echo("Already logged in.", err=True)
            authenticated = True
            return

        if not phone:
            phone = click.prompt(
                "Phone number (with +country code)", type=str
            )
        await client.send_code_request(phone)
        code = click.prompt("Login code", type=str)
        try:
            await client.sign_in(phone=phone, code=code)
        except SessionPasswordNeededError:
            password = click.prompt(
                "2FA password", hide_input=True, type=str
            )
            await client.sign_in(password=password)
        authenticated = True
        click.echo("Login successful; session saved.", err=True)
    finally:
        await client.disconnect()
        if authenticated:
            _tighten_session_perms_from_client(client)


def _tighten_session_perms_from_client(client: Any) -> None:
    """Best-effort ``0o600`` chmod of the Telethon session SQLite file.

    Reads the path from ``client.session.filename`` — the single source
    of truth inside the Telethon client — so we do not re-load config.
    Swallowed on any failure; tightening perms is defence in depth, not
    a hard invariant.
    """
    try:
        filename = getattr(client.session, "filename", None)
        if not isinstance(filename, (str, Path)):
            return
        _tighten_session_perms(Path(filename))
    except (OSError, ValueError):
        return


def _tighten_session_perms(session_path: Path) -> None:
    """Restrict the Telethon session file to ``0o600``.

    The session grants full account access; a shared-machine user must
    not be able to read it. Silently skipped on platforms where chmod
    is a no-op (e.g. Windows).
    """
    sqlite_path = session_path.with_suffix(".session")
    if not sqlite_path.exists():
        return
    try:
        sqlite_path.chmod(0o600)
    except (OSError, NotImplementedError):
        pass
