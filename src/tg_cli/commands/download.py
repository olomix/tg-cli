"""``tg download`` — save photos from a Telegram group into a directory."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import click
from telethon.tl import types as _tl

from ..client import make_client
from ..errors import AuthError, handle_errors
from ._peer import marked_peer_id
from ._resolve import resolve

# Telegram message ids are 32-bit; Telethon raises ``struct.error``
# instead of an RPC error for anything larger.
_MESSAGE_ID = click.IntRange(min=1, max=2**31 - 1)
_DEFAULT_MAX_BYTES = 5 * 1024 * 1024


@click.command()
@click.argument("group")
@click.argument("message_ids", type=_MESSAGE_ID, nargs=-1, required=True)
@click.option(
    "--dir",
    "directory",
    required=True,
    help="Directory to save the photos into; created if missing.",
)
@click.option(
    "--max-bytes",
    "max_bytes",
    type=click.IntRange(min=1),
    default=_DEFAULT_MAX_BYTES,
    show_default=True,
    help="Save the largest size of each photo that is not above this.",
)
@handle_errors
def download(
    group: str,
    message_ids: tuple[int, ...],
    directory: str,
    max_bytes: int,
) -> None:
    """Save the photos of messages MESSAGE_IDS from GROUP into a
    directory and print one JSON result per id, in the order given."""
    _require_writable_dir(directory)
    results = asyncio.run(
        _download_photos(
            group, list(message_ids), os.path.abspath(directory), max_bytes
        )
    )
    click.echo(json.dumps(results))


def _require_writable_dir(path: str) -> None:
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise click.UsageError(
            f"--dir {path}: cannot create the directory ({exc.strerror})."
        ) from exc
    if not os.path.isdir(path) or not os.access(path, os.W_OK | os.X_OK):
        raise click.UsageError(f"--dir {path}: not a writable directory.")


async def _download_photos(
    group: str, message_ids: list[int], directory: str, max_bytes: int
) -> list[dict[str, Any]]:
    client = make_client()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise AuthError()
        entity = await resolve(client, group)
        group_id = marked_peer_id(entity)
        # For a list of ids Telethon answers in request order, with
        # ``None`` in place of each id that does not exist.
        found = await client.get_messages(entity, ids=message_ids)
        results = []
        for message_id, raw in zip(message_ids, found, strict=True):
            path = os.path.join(directory, f"{group_id}_{message_id}.jpg")
            results.append(
                await _save_photo(client, raw, message_id, path, max_bytes)
            )
        return results
    finally:
        await client.disconnect()


async def _save_photo(
    client: Any, raw: Any, message_id: int, path: str, max_bytes: int
) -> dict[str, Any]:
    if raw is None:
        return _skipped(message_id, "not_found")
    variants = _real_variants(raw)
    if not variants:
        return _skipped(message_id, "not_photo")
    fitting = [(size, v) for size, v in variants if size <= max_bytes]
    if not fitting:
        return _skipped(message_id, "too_large")
    _, chosen = max(fitting, key=lambda pair: pair[0])
    # Named by type string: Telethon ignores a ``PhotoSizeProgressive``
    # passed as an object and then downloads nothing.
    await client.download_media(raw, file=path, thumb=chosen.type)
    return {
        "id": message_id,
        "status": "saved",
        "path": path,
        "bytes": os.path.getsize(path),
        "reason": None,
    }


def _skipped(message_id: int, reason: str) -> dict[str, Any]:
    return {
        "id": message_id,
        "status": "skipped",
        "path": None,
        "bytes": None,
        "reason": reason,
    }


def _real_variants(raw: Any) -> list[tuple[int, Any]]:
    """Return ``(declared bytes, variant)`` for each size of the
    message's photo that holds a real image; empty when the message is
    not a photo post."""
    media = getattr(raw, "media", None)
    # A link preview's image is a ``MessageMediaWebPage`` and an image
    # sent as a file a ``MessageMediaDocument``; neither is a photo post.
    if not isinstance(media, _tl.MessageMediaPhoto):
        return []
    # An expired self-destructing photo has no ``Photo`` payload.
    if not isinstance(media.photo, _tl.Photo):
        return []
    variants = []
    for variant in media.photo.sizes:
        size = _declared_bytes(variant)
        if size is not None:
            variants.append((size, variant))
    return variants


def _declared_bytes(variant: Any) -> int | None:
    # Everything else (stripped preview, outline path, empty size, types
    # not known today) is a placeholder, not a downloadable image.
    if isinstance(variant, _tl.PhotoSize):
        return variant.size
    if isinstance(variant, _tl.PhotoSizeProgressive):
        return max(variant.sizes)
    if isinstance(variant, _tl.PhotoCachedSize):
        return len(variant.bytes)
    return None
