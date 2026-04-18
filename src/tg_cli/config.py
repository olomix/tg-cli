"""Config loader for tg-cli.

Reads ``config.toml`` from the config directory and returns a validated
``Config`` object. The directory defaults to ``~/.config/tg-cli/`` and can be
overridden with the ``TG_CLI_CONFIG_DIR`` environment variable.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

CONFIG_FILENAME = "config.toml"
SESSION_BASENAME = "session"
ENV_CONFIG_DIR = "TG_CLI_CONFIG_DIR"

SETUP_INSTRUCTIONS = (
    "Telegram credentials not configured at {path}.\n"
    "\n"
    "One-time setup:\n"
    "  1. Visit https://my.telegram.org/apps and register an application\n"
    "     to obtain api_id (int) and api_hash (str).\n"
    "  2. Create {path} with contents:\n"
    "\n"
    "         api_id = 1234567\n"
    '         api_hash = "abcdef0123456789abcdef0123456789"\n'
    "\n"
    "  3. Run `tg login` to authenticate."
)


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    """Validated configuration used by all tg-cli commands."""

    api_id: int
    api_hash: str
    config_dir: Path

    @property
    def session_path(self) -> Path:
        """Path passed to Telethon; it appends ``.session`` automatically."""
        return self.config_dir / SESSION_BASENAME


def default_config_dir() -> Path:
    """Return the platform-default config directory (no env override)."""
    return Path.home() / ".config" / "tg-cli"


def get_config_dir() -> Path:
    """Resolve the effective config directory, honoring env override."""
    override = os.environ.get(ENV_CONFIG_DIR)
    if override:
        return Path(override).expanduser()
    return default_config_dir()


def load_config(config_dir: Path | None = None) -> Config:
    """Load and validate the config file.

    Raises ``ConfigError`` when the file is missing, malformed, or lacks
    required fields; the message contains actionable setup instructions.
    """
    directory = Path(config_dir) if config_dir else get_config_dir()
    path = directory / CONFIG_FILENAME
    if not path.exists():
        raise ConfigError(SETUP_INSTRUCTIONS.format(path=path))
    # ``path.exists()`` above is a hint, not a guarantee: the file may
    # disappear or flip to unreadable between the check and the open
    # (rotated by a deploy script, mispermissioned on a shared host,
    # etc.). Catch ``OSError`` — parent of ``FileNotFoundError`` and
    # ``PermissionError`` — so those realistic paths surface as a
    # structured ``ConfigError`` and the CLI's JSON-error contract holds.
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Malformed TOML in {path}: {e}") from e
    except OSError as e:
        raise ConfigError(f"Could not read {path}: {e}") from e

    # The config file holds ``api_hash`` — a long-lived Telegram secret.
    # Tighten perms to 0o600 best-effort each load so a stale 0o644 from
    # the user's umask cannot leak it on a shared machine. Mirrors the
    # post-login session chmod in ``commands/login.py``.
    try:
        path.chmod(0o600)
    except (OSError, NotImplementedError):
        pass

    if "api_id" not in data:
        raise ConfigError(
            f"Missing 'api_id' in {path}. "
            "Config must define api_id (int) and api_hash (str)."
        )
    if "api_hash" not in data:
        raise ConfigError(
            f"Missing 'api_hash' in {path}. "
            "Config must define api_id (int) and api_hash (str)."
        )

    api_id = data["api_id"]
    api_hash = data["api_hash"]
    if not isinstance(api_id, int) or isinstance(api_id, bool):
        raise ConfigError(
            f"api_id in {path} must be an int, got "
            f"{type(api_id).__name__}."
        )
    if not isinstance(api_hash, str):
        raise ConfigError(
            f"api_hash in {path} must be a str, got "
            f"{type(api_hash).__name__}."
        )
    return Config(api_id=api_id, api_hash=api_hash, config_dir=directory)
