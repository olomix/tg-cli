"""Standardised error output for tg-cli commands.

Every command emits a single-line JSON object to stderr on failure:

    {"error": "<human message>", "type": "<ErrorType>"}

Claude (the primary consumer) can parse this programmatically without
scraping prose. Commands use :func:`handle_errors` as a decorator to
centralise mapping of known exception types to this shape.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import wraps
from typing import Any

import click
from telethon.errors import FloodWaitError, SessionPasswordNeededError

from .commands._resolve import GroupResolveError
from .commands._time import TimeParseError
from .config import ConfigError


class AuthError(Exception):
    """Raised when a command requires an authorised session but none exists."""


class MessageNotFoundError(Exception):
    """Raised when a referenced message id cannot be fetched."""


NOT_LOGGED_IN_MESSAGE = "Not logged in. Run `tg login` first."


def emit_error(message: str, error_type: str) -> None:
    """Print the standardised error object to stderr.

    Does not exit the process; callers control the exit code.
    """
    payload = {"error": message, "type": error_type}
    click.echo(json.dumps(payload), err=True)


def _classify(exc: BaseException) -> tuple[str, str] | None:
    """Return ``(message, type)`` for known exceptions, ``None`` otherwise."""
    if isinstance(exc, AuthError):
        return str(exc) or NOT_LOGGED_IN_MESSAGE, "AuthError"
    if isinstance(exc, MessageNotFoundError):
        return str(exc), "MessageNotFoundError"
    if isinstance(exc, ConfigError):
        return str(exc), "ConfigError"
    if isinstance(exc, TimeParseError):
        return str(exc), "TimeParseError"
    if isinstance(exc, GroupResolveError):
        return str(exc), type(exc).__name__
    if isinstance(exc, FloodWaitError):
        seconds = getattr(exc, "seconds", 0)
        return (
            f"Telegram flood wait; retry after {seconds} seconds.",
            "FloodWaitError",
        )
    if isinstance(exc, SessionPasswordNeededError):
        return (
            "Session requires 2FA re-authentication; run `tg login`.",
            "AuthError",
        )
    return None


def handle_errors(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that maps known exceptions to standardised JSON errors.

    The wrapped function is a Click command callback. Known exceptions
    are caught, emitted as JSON to stderr, and converted into
    :class:`click.exceptions.Exit` so Click's test runner sees a clean
    non-zero exit code. Unknown exceptions propagate unchanged so bugs
    surface as tracebacks during development.
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except (click.exceptions.Exit, click.exceptions.UsageError):
            raise
        except Exception as exc:
            classified = _classify(exc)
            if classified is None:
                raise
            message, error_type = classified
            emit_error(message, error_type)
            raise click.exceptions.Exit(1) from exc

    return wrapper
