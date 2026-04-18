"""TelegramClient factory.

Thin wrapper around ``telethon.TelegramClient`` that centralises session
path resolution so every command uses the same file.
"""

from __future__ import annotations

from telethon import TelegramClient

from .config import Config, ConfigError, load_config


def make_client(config: Config | None = None) -> TelegramClient:
    """Return a ``TelegramClient`` bound to the configured session file.

    The caller is responsible for ``await client.connect()`` and, after
    use, ``await client.disconnect()``. This function does not perform
    network I/O.
    """
    cfg = config if config is not None else load_config()
    # Session file grants full account access; restrict directory to
    # owner-only so a shared-machine user cannot read the session.
    # Convert filesystem failures (TG_CLI_CONFIG_DIR pointing at a regular
    # file, an unwritable parent, etc.) to ConfigError so the CLI's
    # JSON-error contract holds instead of leaking a raw traceback.
    try:
        cfg.config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as e:
        raise ConfigError(
            f"Could not create config directory {cfg.config_dir}: {e}"
        ) from e
    try:
        cfg.config_dir.chmod(0o700)
    except (OSError, NotImplementedError):
        pass
    return TelegramClient(str(cfg.session_path), cfg.api_id, cfg.api_hash)
