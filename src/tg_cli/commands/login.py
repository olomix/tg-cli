"""``tg login`` — interactive Telegram authentication."""

from __future__ import annotations

import asyncio

import click
from telethon.errors import SessionPasswordNeededError

from ..client import make_client
from ..config import ConfigError


@click.command()
@click.option(
    "--phone",
    default=None,
    help="Phone number in +country format; prompted for when omitted.",
)
def login(phone: str | None) -> None:
    """Authenticate with Telegram and persist the session file."""
    try:
        asyncio.run(_run_login(phone))
    except ConfigError as e:
        raise click.ClickException(str(e)) from e


async def _run_login(phone: str | None) -> None:
    client = make_client()
    await client.connect()
    try:
        if await client.is_user_authorized():
            click.echo("Already logged in.", err=True)
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
        click.echo("Login successful; session saved.", err=True)
    finally:
        await client.disconnect()
