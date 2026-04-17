"""TelegramClient factory.

Thin wrapper around ``telethon.TelegramClient`` that centralises session
path resolution so every command uses the same file.
"""

from __future__ import annotations

from telethon import TelegramClient

from .config import Config, load_config


def make_client(config: Config | None = None) -> TelegramClient:
    """Return a ``TelegramClient`` bound to the configured session file.

    The caller is responsible for ``await client.connect()`` and, after
    use, ``await client.disconnect()``. This function does not perform
    network I/O.
    """
    cfg = config if config is not None else load_config()
    cfg.config_dir.mkdir(parents=True, exist_ok=True)
    return TelegramClient(str(cfg.session_path), cfg.api_id, cfg.api_hash)
