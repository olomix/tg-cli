---
name: telegram
description: >
  Read the user's Telegram groups and channels via the `tg` CLI (MTProto
  / Telethon wrapper). Trigger when the user asks to list Telegram
  groups, summarise or digest recent messages in a Telegram group,
  search past Telegram conversations, or fetch a Telegram reply thread.
  This skill is the source of truth for `tg` command names and flags —
  do not guess them from memory.
---

# Telegram Access Skill

Provides read access to the user's Telegram account through the `tg`
CLI. Telegram's Bot API cannot read group history; this skill uses the
user-account MTProto API (Telethon) instead, so it can list dialogs,
fetch recent messages, full-text search within a group, and pull reply
threads.

All commands emit **JSON** to stdout on success and a single-line JSON
error object to stderr on failure (non-zero exit code). Parse output
with `json.loads` / `jq`; do not scrape the prose.

---

## When to Use

Trigger this skill when the user asks to:

- list their Telegram groups/channels
- summarise / digest recent activity in a Telegram group
- find a past Telegram message ("what did X say about Y?")
- pull a reply thread for additional context around a message

Do **not** use this skill for sending messages, managing contacts, or
bot-style interactions — it is read-only.

---

## One-Time Setup (done by the user)

1. Register a personal app at https://my.telegram.org/apps to obtain
   `api_id` (int) and `api_hash` (str).
2. Create `~/.config/tg-cli/config.toml`:
   ```toml
   api_id = 1234567
   api_hash = "abcdef0123456789abcdef0123456789"
   ```
3. Run `tg login` once — interactive phone + code (+ optional 2FA).
   Session persists at `~/.config/tg-cli/session.session`.

If the CLI reports `AuthError`, stop and ask the user to run
`tg login`. Do not try to recover automatically.

---

## Commands

### `tg groups [--type {group,channel,all}] [--limit N] [--pretty]`

List the user's dialogs.

Output: JSON array of objects with shape:
```json
{
  "id": -1001234567890,
  "title": "My Dev Group",
  "type": "supergroup",
  "username": "mydevgroup",
  "member_count": 42
}
```
`type` is one of `group`, `supergroup`, `channel`. `username` and
`member_count` may be `null`.

### `tg messages <group> [--since TIME] [--limit N] [--pretty]`

Fetch recent messages from `<group>`, oldest first.

- `<group>` accepts numeric id, `@username`, or case-insensitive
  substring of the title (errors on ambiguous match).
- `TIME` accepts `24h`, `7d`, `2026-04-15`, `2026-04-15T10:00`.
- Default `--limit` is 100.

Output: JSON array of `Message` objects (see shape below).

### `tg search <group> <query> [--since TIME] [--limit N] [--pretty]`

Full-text search within `<group>`. Newest matches first. Same
`<group>` / `TIME` semantics as `messages`.

Output: JSON array of `Message` objects (possibly empty — exit 0).

### `tg thread <group> <message_id> [--limit N] [--pretty]`

Fetch `<message_id>` and its replies. Root message is returned first,
then replies in chronological order.

Output: JSON array of `Message` objects. Fails with
`MessageNotFoundError` if the id does not exist in `<group>`.

### `tg login [--phone +NNN]`

Interactive only. Do not invoke from Claude — if a command fails with
`AuthError`, tell the user to run `tg login` themselves.

---

## Message JSON Shape

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

`date` is ISO 8601 UTC. `sender_name`, `sender_id`, `reply_to_id` may
be `null`.

## Error JSON Shape (stderr)

```json
{"error": "Not logged in. Run `tg login` first.", "type": "AuthError"}
```

Known `type` values include `AuthError`, `ConfigError`,
`TimeParseError`, `GroupResolveError`, `AmbiguousGroupError`,
`MessageNotFoundError`, `FloodWaitError`.

For `FloodWaitError`, the message includes `retry after N seconds` —
surface this to the user; do not auto-retry.

---

## Common Workflows

### Daily digest of one group

```bash
tg groups --type group
# pick the right group from the JSON output
tg messages "<group title or @username>" --since 24h
# summarise the returned messages array into a digest
```

### "What did X say about Y?"

```bash
tg search "<group>" "Y" --since 30d
# optionally for context around a match:
tg thread "<group>" <message_id>
```

### Cross-group scan

1. `tg groups --type group` → parse JSON → iterate.
2. For each interesting group, `tg messages <id> --since 24h`.
3. Merge and summarise.

Keep `--limit` bounded (default 100) to avoid rate limits. If a
`FloodWaitError` is returned, report the wait time and stop.

---

## Notes for Claude

- Always parse output with `json.loads`; never regex the prose.
- Prefer numeric `id` (from `tg groups`) over title when scripting
  multiple calls — it avoids the title-substring ambiguity error.
- The session file grants full account access; never print it, copy
  it, or include it in any tool output.
- If `tg` is not installed, tell the user to run
  `uv tool install .` (or `pipx install .`) from the repo.
