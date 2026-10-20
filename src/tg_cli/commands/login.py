"""``tg login`` — interactive Telegram authentication."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import click
import qrcode
from telethon.errors import SessionPasswordNeededError
from telethon.tl.types import auth

from ..client import make_client
from ..errors import handle_errors


@click.command()
@click.option(
    "--phone",
    default=None,
    help="Phone number in +country format; prompted for when omitted.",
)
@click.option(
    "--qr",
    "use_qr",
    is_flag=True,
    help="Log in by scanning a QR code with the phone app instead of "
    "typing a code.",
)
@handle_errors
def login(phone: str | None, use_qr: bool) -> None:
    """Authenticate with Telegram and persist the session file."""
    asyncio.run(_run_login(phone, use_qr))


async def _run_login(phone: str | None, use_qr: bool = False) -> None:
    client = make_client()
    authenticated = False
    await client.connect()
    try:
        if await client.is_user_authorized():
            click.echo("Already logged in.", err=True)
            authenticated = True
            return

        try:
            if use_qr:
                await _qr_login(client)
            else:
                if not phone:
                    phone = click.prompt(
                        "Phone number (with +country code)", type=str
                    )
                code = await _request_code(client, phone)
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


async def _qr_login(client: Any) -> None:
    """Show login QR codes until the phone app scans one.

    Each token expires after about 30 seconds, so a fresh QR code is
    drawn whenever the current one times out.
    """
    qr_login = await client.qr_login()
    while True:
        click.echo(
            "Scan with the Telegram phone app: Settings > Devices > "
            "Link Desktop Device.",
            err=True,
        )
        _print_qr(qr_login.url)
        try:
            await qr_login.wait()
            return
        except asyncio.TimeoutError:
            await qr_login.recreate()


def _print_qr(url: str) -> None:
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.print_ascii(out=click.get_text_stream("stderr"), invert=True)


async def _request_code(client: Any, phone: str) -> str:
    """Send the login code and prompt for it, resending on empty input.

    Telegram's ``next_type`` names the fallback channel (usually SMS)
    used when the first delivery never arrives.
    """
    sent = await client.send_code_request(phone)
    while True:
        if isinstance(sent.type, auth.SentCodeTypeSetUpEmailRequired):
            raise click.ClickException(
                "Telegram requires a login email for this account before "
                "it sends a code; set one in the official Telegram app "
                "(Settings > Privacy and Security) and retry."
            )
        click.echo(_describe_delivery(sent.type), err=True)
        next_type = getattr(sent, "next_type", None)
        if next_type is None:
            click.echo(
                "Telegram offers no other delivery method for this code.",
                err=True,
            )
            return click.prompt("Login code", type=str)
        code = click.prompt(
            "Login code (empty to resend via "
            f"{_delivery_hint(next_type)})",
            default="",
            show_default=False,
            type=str,
        ).strip()
        if code:
            return code
        sent = await client.send_code_request(phone, force_sms=True)


_DELIVERY_HINTS: dict[type, str] = {
    auth.SentCodeTypeApp: "a message from 'Telegram' in your Telegram app "
    "on another logged-in device (not SMS)",
    auth.SentCodeTypeSms: "SMS",
    auth.SentCodeTypeSmsWord: "SMS",
    auth.SentCodeTypeSmsPhrase: "SMS",
    auth.SentCodeTypeFirebaseSms: "SMS",
    auth.SentCodeTypeFragmentSms: "Fragment (fragment.com)",
    auth.SentCodeTypeCall: "a phone call",
    auth.SentCodeTypeFlashCall: "a phone call",
    auth.SentCodeTypeMissedCall: "a missed call; the code is the last "
    "digits of the calling number",
    auth.SentCodeTypeEmailCode: "your login email",
}


def _delivery_hint(code_type: Any) -> str:
    if isinstance(code_type, auth.CodeTypeSms):
        return "SMS"
    if isinstance(code_type, (auth.CodeTypeCall, auth.CodeTypeFlashCall)):
        return "a phone call"
    if isinstance(code_type, auth.CodeTypeMissedCall):
        return "a missed call"
    if isinstance(code_type, auth.CodeTypeFragmentSms):
        return "Fragment (fragment.com)"
    return _DELIVERY_HINTS.get(type(code_type), type(code_type).__name__)


def _describe_delivery(code_type: Any) -> str:
    return f"Code sent via {_delivery_hint(code_type)}."


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
    """Restrict the Telethon session files to ``0o600``.

    The session grants full account access; a shared-machine user must
    not be able to read it. SQLite may create ``-wal``/``-shm`` sidecars
    alongside the main ``.session`` file when WAL mode is active — those
    hold the same secrets, so they must be chmodded too. Silently
    skipped on platforms where chmod is a no-op (e.g. Windows) and on
    sidecars that don't exist (WAL mode not in use).
    """
    sqlite_path = session_path.with_suffix(".session")
    for path in (
        sqlite_path,
        sqlite_path.with_name(sqlite_path.name + "-wal"),
        sqlite_path.with_name(sqlite_path.name + "-shm"),
    ):
        try:
            path.chmod(0o600)
        except (FileNotFoundError, NotImplementedError):
            continue
        except OSError:
            continue
