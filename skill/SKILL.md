---
name: telegram
description: >
  Read the user's Telegram groups and channels via the `tg` CLI (MTProto
  / Telethon wrapper). Trigger when the user asks to list Telegram
  groups, summarise or digest recent messages in a Telegram group,
  read everything posted since a known message, search past Telegram
  conversations, fetch a Telegram reply thread or specific messages by
  id, or save photos from a Telegram group. This skill is the source of
  truth for `tg` command names and flags — do not guess them from
  memory.
---

# Telegram Access Skill

Provides read access to the user's Telegram account through the `tg`
CLI. Telegram's Bot API cannot read group history; this skill uses the
user-account MTProto API (Telethon) instead, so it can list dialogs,
fetch recent messages, read everything after a known message id,
full-text search within a group, pull reply threads, fetch messages by
id, and save photos to a local directory.

All commands emit **JSON** to stdout on success and a single-line JSON
error object to stderr on failure (non-zero exit code). Parse output
with `json.loads` / `jq`; do not scrape the prose. A mistake on the
command line (bad value, conflicting options) is a `UsageError` with
exit code 2; every other failure exits with 1.

---

## When to Use

Trigger this skill when the user asks to:

- list their Telegram groups/channels
- summarise / digest recent activity in a Telegram group
- read everything posted in a group since the last time it was read
- find a past Telegram message ("what did X say about Y?")
- pull a reply thread for additional context around a message
- fetch specific messages by id (e.g. the parent of a reply)
- look at photos posted in a group

Do **not** use this skill for sending messages, managing contacts, or
bot-style interactions — it is read-only towards Telegram. The one
command that writes anything is `tg download`, and it saves photos only
inside the directory given to `--dir`.

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

### `tg messages <group> [--since TIME] [--after-id N [--through-id M]] [--limit N] [--pretty]`

Fetch recent messages from `<group>`, oldest first.

- `<group>` accepts numeric id, `@username`, or case-insensitive
  substring of the title (errors on ambiguous match).
- `TIME` accepts `24h`, `7d`, `2026-04-15`, `2026-04-15T10:00`.
- Default `--limit` is 100. Without `--after-id` the command returns
  the **newest** `--limit` messages, so anything beyond the limit is
  cut off.
- `--after-id N` returns only messages with an id greater than `N`,
  and switches to the **oldest** `--limit` messages of the range,
  still oldest first. `--after-id 0` starts at the first message.
- `--through-id M` returns only messages with an id up to and
  including `M`; together the two select `(N, M]`. Without it the
  range runs up to the newest message.
- Both ids are whole numbers from 0 to 2147483647. `--after-id` cannot
  be combined with `--since`, and `--through-id` requires
  `--after-id`: either mistake is a `UsageError` (exit 2).
- A `--through-id` at or below `--after-id` returns `[]` (exit 0).

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

### `tg get <group> <id>... [--pretty]`

Fetch specific messages by id, e.g. the parent of a reply that falls
outside a fetched range (`tg thread` walks down from a root, not up to
a parent).

- At least one id is required, each a whole number from 1 to
  2147483647.
- Ids that do not exist are left out; the command still exits 0.

Output: JSON array of `Message` objects in the order the ids were given
(possibly empty — exit 0).

### `tg download <group> <id>... --dir DIR [--max-bytes N]`

Save the photos attached to the given messages into `DIR`.

- `--dir` is required. The directory is created if missing (mode
  0700); one that cannot be created or written to is a `UsageError`
  naming the path (exit 2).
- Files are named `<group_id>_<message_id>.jpg`, e.g.
  `-1001234567890_12345.jpg`. An existing file is overwritten.
- The largest size of each photo that is not above `--max-bytes`
  (default 5242880, i.e. 5 MiB) is saved; a tight budget yields a
  smaller picture.
- Only photo posts (`media_kind` `photo`) are saved. Images sent as
  files (`document`) and link-preview pictures (`webpage`) are
  skipped.
- Message ids follow the same rules as for `tg get`. There is no
  `--pretty`.

Output: JSON array with one object per requested id, in the order
given:
```json
{
  "id": 12345,
  "status": "saved",
  "path": "/home/alice/photos/-1001234567890_12345.jpg",
  "bytes": 48213,
  "reason": null
}
```
`status` is `saved` or `skipped`. For a saved photo `path` is absolute
and `bytes` is the file size. For a skipped id `path` and `bytes` are
`null` and `reason` is `not_found` (no such message), `not_photo` (not
a photo post, or the photo has expired) or `too_large` (no size fits
`--max-bytes`). Skips are normal results — exit 0.

A failure while downloading or writing ends the command with a
non-zero exit: `DownloadError` (dropped connection, empty download,
disk error), `TelegramError` or `FloodWaitError`. Photos saved before
the failure stay in place. Read a saved photo with the Read tool to
look at it.

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
  "group_id": -1001234567890,
  "sender_username": "alice",
  "topic_id": null,
  "media_kind": "photo",
  "grouped_id": null,
  "urls": [],
  "forward": null,
  "link": "https://t.me/mydevgroup/12345"
}
```

Emitted by `tg messages`, `tg search`, `tg thread` and `tg get`.

`date` is ISO 8601 UTC. `sender_name`, `sender_id`, `reply_to_id` may
be `null`.

- `sender_username` — the sender's `@username` without the `@`, or
  `null`.
- `topic_id` — the forum topic the message was posted in; `null`
  outside forum topics, in a forum's General topic, and on the service
  message that creates a topic.
- `media_kind` — one of `photo`, `video`, `gif`, `sticker`, `voice`,
  `audio`, `document`, `webpage`, `poll`, `other`; `null` when the
  message has no media (service messages included). A link preview is
  `webpage` even when it shows a picture; an image sent as a file is
  `document`. Only `photo` can be fetched with `tg download`.
- `grouped_id` — the album id shared by the messages of one album, or
  `null`. Treat messages with the same `grouped_id` as one post.
- `urls` — the URLs in the message, in order of appearance and without
  duplicates: plain URLs and the hidden targets of text links. Empty
  array when there are none.
- `forward` — `null`, or the origin of a forwarded message as an
  object with the keys `from_id` (a marked peer id like `group_id`),
  `from_name` and `date` (ISO 8601); each of the three may be `null`.
- `link` — a permalink to the message; use it when citing a message to
  the user. One of four shapes:
  - public group or channel: `https://t.me/<username>/<id>`
  - public forum topic: `https://t.me/<username>/<topic_id>/<id>`
  - private supergroup or channel: `https://t.me/c/<channel id>/<id>`
  - private forum topic:
    `https://t.me/c/<channel id>/<topic_id>/<id>`

  `<channel id>` is the channel's bare id (`1234567890` for a
  `group_id` of `-1001234567890`). `link` is `null` in a basic
  (non-super) group, which has no message permalinks. Take `link` from
  the output; do not build it by hand.

## Error JSON Shape (stderr)

```json
{"error": "Not logged in. Run `tg login` first.", "type": "AuthError"}
```

Known `type` values include `AuthError`, `ConfigError`,
`TimeParseError`, `GroupNotFoundError`, `AmbiguousGroupError`,
`MessageNotFoundError`, `DownloadError`, `FloodWaitError`,
`TelegramError`, `UsageError`.

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

### Read everything since last time

`--since` with the default limit keeps only the newest 100 messages.
To read every message after a stored cursor `C` (the id of the last
message already read), page by id:

```bash
# 1. fix the upper bound: H is the id of the newest message
tg messages "<group>" --limit 1
# 2. read one page of the range (C, H]
tg messages "<group>" --after-id <C> --through-id <H> --limit 100
# 3. a full page (100 messages): set C to the id of its last message
#    and repeat step 2; a shorter page means the range is drained
```

- Once the range is drained, `H` is the cursor for the next run.
  Messages that arrive during the run have ids above `H` and are left
  for that run.
- Ids are not contiguous; gaps are normal.
- Treat a page as an error if its ids are not strictly increasing,
  fall outside `(C, H]`, or do not advance `C`.
- If `H` is below the stored cursor (messages were deleted), there is
  nothing new. Never move a cursor backwards.
- A reply whose parent lies outside the range: fetch the parent with
  `tg get "<group>" <reply_to_id>`.
- To look at photos in the range, pass the ids of the messages whose
  `media_kind` is `photo` to
  `tg download "<group>" <id>... --dir <dir>`.

### "What did X say about Y?"

```bash
tg search "<group>" "Y" --since 30d
# optionally for context around a match:
tg thread "<group>" <message_id>
```

### Cross-group scan

1. `tg groups --type group` → parse JSON → iterate.
2. For each interesting group, `tg messages --since 24h -- <id>`.
3. Merge and summarise.

Keep `--limit` bounded (default 100) to avoid rate limits. If a
`FloodWaitError` is returned, report the wait time and stop.

---

## Notes for Claude

- Always parse output with `json.loads`; never regex the prose.
- Prefer numeric `id` (from `tg groups`) over title when scripting
  multiple calls — it avoids the title-substring ambiguity error.
- A negative numeric id (e.g. `-1001234567890`) is mistaken for an
  option. Put the options first and the id after a `--` separator:
  `tg messages --since 24h -- -1001234567890`. Everything after `--`
  is positional, so for `tg get` and `tg download` an option placed
  after the ids is rejected:
  `tg download --dir photos -- -1001234567890 5` works,
  `tg download -- -1001234567890 5 --dir photos` does not.
- Migrated basic chats are transparently redirected. If the user (or
  older notes) references a legacy basic-chat id, `@username`, or old
  title substring, `tg messages` / `tg search` / `tg thread` /
  `tg get` / `tg download` resolve to the supergroup it was migrated
  to. The `group_id` on returned messages is the resolved supergroup's
  `-100…` id, **not** the id passed on the command line — do not cache
  an id-to-group_id mapping from the request side; always read
  `group_id` off the response.
  Migrated zombies are also filtered from `tg groups`, so a fresh
  listing always shows the supergroup id.
- The session file grants full account access; never print it, copy
  it, or include it in any tool output.
- If `tg` is not installed, tell the user to run
  `uv tool install .` (or `pipx install .`) from the repo.
