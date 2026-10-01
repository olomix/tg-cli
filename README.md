# tg-cli

A minimal Python CLI that wraps [Telethon](https://github.com/LonamiWebs/Telethon)
(MTProto) so [Claude Code](https://claude.com/claude-code) can read your
Telegram groups on your behalf — list groups, fetch recent messages,
read everything after a known message id, search within a group, pull
reply threads, fetch messages by id, and save photos.

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
> paste it into chats. `tg` is read-only towards Telegram: nothing in
> this repo sends messages or changes Telegram state. Apart from its
> own session file, the only thing it writes is the photos saved by
> `tg download`, and those go only inside the directory you pass to
> `--dir`.

## Install

Requires Python 3.10+.

```bash
# install from a local checkout
git clone <repo-url> ~/src/tg-cli
cd ~/src/tg-cli
uv tool install .          # or: pipx install .
```

This exposes a `tg` command on your `PATH`.

To upgrade an existing install, update the checkout and reinstall:

```bash
cd ~/src/tg-cli
git pull
uv tool install --reinstall .
tg --version               # tg, version 0.2.0
```

The id range of `tg messages` (`--after-id`, `--through-id`), `tg get`,
`tg download` and the seven message fields from `sender_username` to
`link` need 0.2.0 or later. An older `tg` rejects the new commands and
options with a `UsageError` such as `No such command 'get'`.

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
output; `tg download` has no such option and always prints a single
line. A mistake on the command line (unknown option, bad value,
conflicting options) is reported the same way with type `UsageError`
and exit code 2; every other failure exits with 1.

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

### `tg messages <group> [--since TIME] [--after-id N [--through-id M]] [--limit N] [--pretty]`

Fetch recent messages from `<group>`, oldest first.

```bash
tg messages "My Dev Group" --since 24h
tg messages @mydevgroup --since 7d --limit 200
tg messages --since 2026-04-15T10:00 -- -1001234567890
tg messages @mydevgroup --after-id 4200 --through-id 4950 --limit 100
```

`<group>` accepts a numeric id, `@username`, or a case-insensitive
substring of the title (errors on ambiguous match).

> **Negative numeric ids.** Raw supergroup/channel ids start with `-`
> (e.g. `-1001234567890`), which the CLI parser mistakes for an option
> and rejects with `No such option: -1...`. Pass options first and put
> the id after a `--` separator, or use the `@username` / title form
> instead. The same applies to `tg search`, `tg thread`, `tg get` and
> `tg download`. Everything after `--` is read as a positional
> argument, so for `tg get` and `tg download`, which take a list of
> message ids, an option placed after the ids is taken for one more id
> and rejected: `tg download --dir photos -- -1001234567890 5` works,
> `tg download -- -1001234567890 5 --dir photos` does not.

> **Migrated basic chats.** Legacy basic-chat ids (and old title
> substrings) are transparently redirected to the supergroup they were
> migrated to, so an id you copy-pasted from an older listing keeps
> working. This applies equally to numeric ids, `@username`, and title
> substrings, and covers `tg messages`, `tg search`, `tg thread`,
> `tg get` and `tg download`. The `group_id` field on returned
> messages reflects the resolved supergroup's `-100…` id, not the id
> you passed on the command line.

`TIME` accepts `24h`, `7d`, `2026-04-15`, or `2026-04-15T10:00` (UTC).

`--limit` defaults to 100. Without `--after-id` the command returns the
**newest** `--limit` messages (those after `--since`, when given), so
on a busy day anything beyond the limit is cut off. To read everything
after a known message, use the id range instead.

**Id range.** `--after-id N` returns only messages with an id greater
than `N`, and `--through-id M` only those with an id up to and
including `M`, so together they select `(N, M]`. With `--after-id` the
command returns the **oldest** `--limit` messages of the range, still
oldest first.

- Without `--through-id` the range runs up to the newest message.
- Both take a whole number from 0 to 2147483647. `--after-id 0` starts
  at the first message of the group.
- `--after-id` cannot be combined with `--since`, and `--through-id`
  requires `--after-id`. Either mistake is a `UsageError` (exit 2).
- A `--through-id` at or below `--after-id` selects nothing: the output
  is `[]` and the exit code 0.

**Paging through a range.** To read every message posted after a
stored cursor `C` (the id of the last message you have already read):

1. Read the newest id `H`: run `tg messages <group> --limit 1` and take
   the `id` of the message it returns.
2. Run `tg messages <group> --after-id C --through-id H --limit L`.
3. If the page holds `L` messages, set `C` to the id of its last
   message and repeat step 2. A page shorter than `L` means the range
   is drained, and `H` is the cursor for the next run.

Fixing `H` first means messages that arrive during the run are left for
the next run instead of being half-read. Ids are not contiguous; gaps
are normal, so do not count on a page covering a fixed span of ids.

A caller should treat a page as an error if its ids are not strictly
increasing, fall outside `(C, H]`, or do not advance `C`. An empty
page is not an error: it has no ids to check and ends the range, as
happens when the previous page was exactly full or nothing new was
posted. If the newest visible id is below the stored cursor (messages
were deleted), there is nothing new; never move a cursor backwards.

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

### `tg get <group> <id>... [--pretty]`

Fetch specific messages by id, for example the parent of a reply that
falls outside the range you fetched. The output is an array of messages
in the order the ids were given.

```bash
tg get @mydevgroup 12345
tg get "My Dev Group" 12345 12350 12361
tg get -- -1001234567890 12345
```

At least one id is required, each a whole number from 1 to 2147483647.
Ids that do not exist are left out and the command still exits 0, so an
empty array is a valid result.

### `tg download <group> <id>... --dir DIR [--max-bytes N]`

Save the photos attached to the given messages into `DIR`.

```bash
tg download @mydevgroup 12345 12346 --dir photos
tg download @mydevgroup 12345 --dir photos --max-bytes 500000
tg download --dir photos -- -1001234567890 12345
```

- `--dir` is required. The directory is created if missing, with mode
  0700. A directory that cannot be created or written to is a
  `UsageError` naming the path (exit 2), reported before anything is
  fetched from Telegram.
- Files are named `<group_id>_<message_id>.jpg`, for example
  `-1001234567890_12345.jpg`. An existing file is overwritten.
- Telegram keeps several sizes of each photo. The largest one that is
  not above `--max-bytes` (default 5242880, i.e. 5 MiB) is saved, so a
  tight budget yields a smaller picture rather than a failure.
- Only photo posts are saved (`media_kind` is `photo`). An image sent
  as a file is a document, and the picture of a link preview is not a
  photo post; both are skipped.
- Message ids follow the same rules as for `tg get`.

The output is an array with one object per requested id, in the order
given. A saved photo:
```json
{
  "id": 12345,
  "status": "saved",
  "path": "/home/alice/photos/-1001234567890_12345.jpg",
  "bytes": 48213,
  "reason": null
}
```

A skipped id:
```json
{
  "id": 12346,
  "status": "skipped",
  "path": null,
  "bytes": null,
  "reason": "not_photo"
}
```

`status` is `saved` or `skipped`. For a saved photo `path` is absolute
and `bytes` is the size of the file on disk. For a skipped id both are
null and `reason` is one of:

- `not_found` — the group has no message with this id;
- `not_photo` — the message is not a photo post, or its photo is no
  longer available (an expired self-destructing photo);
- `too_large` — every size of the photo is above `--max-bytes`, or
  the downloaded file turned out larger than that and was deleted.

Skips are normal results: the command still exits 0. A failure while
downloading or writing ends the command with a JSON error and a
non-zero exit: `DownloadError` for a dropped connection, an empty
download or a disk error, `TelegramError` or `FloodWaitError` when
Telegram refuses the request. Photos saved before the failure stay in
place.

Each photo is downloaded under a temporary hidden name inside `DIR`
and renamed into place once complete, so a partial file never carries
the final name. A download that fails or is interrupted removes its
temporary file; only if that cannot be done either does a hidden
`.jpg` file stay behind in `DIR`. A symlink already sitting at the
target name is replaced, not followed, so nothing is written outside
`DIR`.

### Message JSON shape

`tg messages`, `tg search`, `tg thread` and `tg get` emit the same
object:

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

- `sender_username` — the sender's `@username` without the `@` (the
  first active one when the sender has several), or null.
- `topic_id` — the id of the forum topic the message was posted in;
  null outside forum topics. Messages in a forum's General topic are
  null too, and so is the service message that creates a topic.
- `media_kind` — what is attached: `photo`, `video`, `gif`, `sticker`,
  `voice`, `audio`, `document`, `webpage`, `poll` or `other`; null
  when the message has no media, which includes service messages. A
  link preview is `webpage` even when it shows a picture, and an image
  sent as a file is `document`.
- `grouped_id` — the album id shared by the messages of one album, or
  null.
- `urls` — the URLs in the message, in order of appearance and without
  duplicates: plain URLs and the hidden targets of text links. An
  empty array when there are none.
- `forward` — null, or for a forwarded message its origin as an object
  with the keys `from_id` (a marked peer id, the form `group_id`
  uses), `from_name` and `date` (ISO 8601); each of the three can be
  null.
- `link` — a permalink to the message, in one of four shapes:
  - public group or channel: `https://t.me/<username>/<id>`
  - public forum topic: `https://t.me/<username>/<topic_id>/<id>`
  - private supergroup or channel: `https://t.me/c/<channel id>/<id>`
  - private forum topic:
    `https://t.me/c/<channel id>/<topic_id>/<id>`

  `<channel id>` is the channel's bare id: `1234567890` for a
  `group_id` of `-1001234567890`. `link` is null in a basic
  (non-super) group, where Telegram has no message permalinks. A
  message with a null `topic_id` gets the shape without a topic, so
  the service message that creates a topic links like an ordinary
  message.

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

**`DownloadError`.** `tg download` could not fetch or save a photo:
the connection dropped, Telegram sent no data, or writing to `--dir`
failed (disk full, permissions). Photos saved earlier in the same run
stay in place; fix the cause and run the command again for the
remaining ids.

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
