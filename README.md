# tg-cli

A minimal Python CLI that wraps [Telethon](https://github.com/LonamiWebs/Telethon)
(MTProto) so [Claude Code](https://claude.com/claude-code) can read your
Telegram groups on your behalf — list groups, fetch recent messages,
search within a group, and pull reply threads.

Bundled with a Claude Code skill (`skill/SKILL.md`) that documents the
CLI for the model so it does not have to guess command names or flags.

## Why MTProto?

Telegram's Bot API cannot read group history — a bot only sees messages
explicitly addressed to it. MTProto (the user-account API) is the only
way to search past group conversations. `tg-cli` uses Telethon to speak
MTProto with your own account.

> **Security note:** the session file at
> `~/.config/tg-cli/session.session` grants full access to your Telegram
> account. Treat it like a credential: do not commit it, share it, or
> paste it into chats. The skill is strictly read-only; nothing in this
> repo sends messages or mutates state.

## Install

Requires Python 3.10+.

```bash
# install from a local checkout
git clone <repo-url> ~/src/tg-cli
cd ~/src/tg-cli
uv tool install .          # or: pipx install .
```

This exposes a `tg` command on your `PATH`.

## One-Time Setup

1. Register a personal app at https://my.telegram.org/apps to obtain
   `api_id` (int) and `api_hash` (str). This is free and one-time.
2. Create `~/.config/tg-cli/config.toml`:
   ```toml
   api_id = 1234567
   api_hash = "abcdef0123456789abcdef0123456789"
   ```
   Override the directory with `TG_CLI_CONFIG_DIR=/some/path` if needed.
3. Authenticate:
   ```bash
   tg login              # interactive: phone prompt + login code
   tg login --phone +15551234567   # skip the phone prompt
   ```
   Prompts for phone number (unless `--phone` is given), login code,
   and (if enabled) 2FA password. The session persists at
   `~/.config/tg-cli/session.session` so subsequent commands run
   non-interactively.

## Commands

All commands emit JSON to stdout on success and a JSON error object to
stderr (with non-zero exit) on failure. Add `--pretty` for indented
output.

### `tg groups [--type {group,channel,all}] [--limit N] [--pretty]`

List your dialogs.

```bash
tg groups --type group | jq '.[] | .title'
```

Example element:
```json
{
  "id": -1001234567890,
  "title": "My Dev Group",
  "type": "supergroup",
  "username": "mydevgroup",
  "member_count": 42
}
```

### `tg messages <group> [--since TIME] [--limit N] [--pretty]`

Fetch recent messages from `<group>`, oldest first.

```bash
tg messages "My Dev Group" --since 24h
tg messages @mydevgroup --since 7d --limit 200
tg messages --since 2026-04-15T10:00 -- -1001234567890
```

`<group>` accepts a numeric id, `@username`, or a case-insensitive
substring of the title (errors on ambiguous match).

> **Negative numeric ids.** Raw supergroup/channel ids start with `-`
> (e.g. `-1001234567890`), which the CLI parser mistakes for an option
> and rejects with `No such option: -1...`. Pass options first and put
> the id after a `--` separator, or use the `@username` / title form
> instead. The same applies to `tg search` and `tg thread`.

> **Migrated basic chats.** Legacy basic-chat ids (and old title
> substrings) are transparently redirected to the supergroup they were
> migrated to, so an id you copy-pasted from an older listing keeps
> working. This applies equally to numeric ids, `@username`, and title
> substrings, and covers `tg messages`, `tg search`, and `tg thread`.
> The `group_id` field on returned messages reflects the resolved
> supergroup's `-100…` id, not the id you passed on the command line.

`TIME` accepts `24h`, `7d`, `2026-04-15`, or `2026-04-15T10:00` (UTC).

### `tg search <group> <query> [--since TIME] [--limit N] [--pretty]`

Full-text search within `<group>`, newest matches first.

```bash
tg search "My Dev Group" "deploy" --since 30d
```

### `tg thread <group> <message_id> [--limit N] [--pretty]`

Fetch a root message and its replies. The root message is returned
first, then replies in chronological (oldest-first) order.

```bash
tg thread "My Dev Group" 12345
```

### Message JSON shape

```json
{
  "id": 12345,
  "date": "2026-04-17T10:23:45+00:00",
  "sender_id": 98765,
  "sender_name": "Alice",
  "text": "hello world",
  "reply_to_id": null,
  "group_id": -1001234567890
}
```

### Error JSON shape (stderr)

```json
{"error": "Not logged in. Run `tg login` first.", "type": "AuthError"}
```

## Claude Code Skill

The `skill/` directory contains a Claude Code skill (`SKILL.md`) that
tells Claude when and how to use `tg`. Install it as a symlink:

```bash
./scripts/install-skill.sh
```

This links `skill/` to `~/.claude/skills/telegram/`. The script is
idempotent and refuses to clobber an existing file. Restart your Claude
Code session after installing so the skill is picked up.

See [`skill/SKILL.md`](skill/SKILL.md) for the full skill contents
(command reference, workflows, and notes Claude follows when calling
`tg`).

## Troubleshooting

**`FloodWaitError: retry after N seconds`.** Telegram rate-limited the
account. Wait the reported number of seconds before retrying. Reduce
`--limit` or avoid tight loops across many groups.

**`AuthError: Not logged in. Run \`tg login\` first.`.** Session is
missing or was invalidated (password change, security event, or
explicit logout). Run `tg login` again.

**Session corruption / `database is locked`.** A stale session file can
linger after a crash. Remove it and re-login:
```bash
rm ~/.config/tg-cli/session.session
tg login
```

**`ConfigError`.** Config file missing or malformed. Recreate
`~/.config/tg-cli/config.toml` per the setup section above.

**`AmbiguousGroupError`.** The title substring matched more than one
dialog. Use the numeric `id` from `tg groups` instead.

**`MessageNotFoundError`.** The message id does not exist in the given
group. Confirm via `tg messages` or `tg search`.

## Development

```bash
uv sync --all-extras          # install dev deps
uv run pytest -v              # run the test suite
uv run pytest --cov=tg_cli    # coverage
uv run ruff check src tests   # lint
shellcheck scripts/install-skill.sh
```

Tests mock `TelegramClient`; no live API calls are made from the test
suite. For end-to-end verification, use the commands against a
throwaway group from a shell after `tg login`.

## License

MIT.
