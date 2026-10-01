# Incremental fetch, richer messages, get-by-id and photo download

Date: 2026-10-01

## Goal

Make `tg` usable as the data source for an unattended digest job that
reads every message posted since its previous run, links back to the
source messages, and looks at photos.

Today that job cannot be built correctly on top of `tg`:

- `tg messages` returns the **newest** `--limit` messages. A caller that
  stores the highest id it saw and asks again later silently loses
  everything beyond the limit on a busy day.
- The message JSON carries no media information, no permalink, no topic
  id, no forward origin, and drops URLs hidden behind text links.
- There is no way to fetch specific messages by id (reply parents) or to
  download a photo.

All changes are additive. Existing commands keep their current output and
semantics when the new options are not used.

## Non-goals

- `tg topics` and `tg messages --topic`. They stay in
  `docs/plans/20260419-forum-topics-support.md`. This design implements
  only the `topic_id` field from that plan (its tasks 1 and 2, minus the
  `Topic` dataclass); the plan is updated to say so.
- Reaction counts, `edit_date`, re-reading edited messages.
- Downloading anything other than photos (video, documents, stickers).
- Sending messages or changing any Telegram state.

## 1. Incremental range on `tg messages`

New options:

- `--after-id N` — only messages with id greater than `N` (exclusive).
- `--through-id M` — only messages with id at most `M` (inclusive).
  Requires `--after-id`.

Rules:

- With `--after-id`, the command returns the **oldest** `--limit`
  messages in the range, oldest first. Without it, behaviour is unchanged
  (newest `--limit`, reversed).
- `--after-id` and `--since` are mutually exclusive: usage error, exit 2.
- `--through-id` without `--after-id`: usage error, exit 2.
- `--through-id` lower than or equal to `--after-id`: empty array, exit 0.
- `--limit` keeps its default of 100.

Telethon mapping: `iter_messages(entity, limit=limit, min_id=N,
max_id=M + 1, reverse=True)`. Both bounds are exclusive in Telethon,
hence `M + 1`.

Paging contract for callers, documented in README and the skill:

1. Read the newest id `H` with `tg messages --limit 1`.
2. Call `tg messages --after-id C --through-id H --limit L`.
3. If the page holds `L` messages, set `C` to the last id and repeat.
   A page shorter than `L` means the range is drained.

Ids are not contiguous; gaps are normal. Fixing `H` first means messages
that arrive during the run are left for the next run instead of being
half-read.

## 2. New `Message` fields

Added to the JSON emitted by `messages`, `search`, `thread` and `get`:

| Field | Type | Meaning |
|---|---|---|
| `sender_username` | string or null | Sender's `@username` without the `@`. |
| `topic_id` | int or null | Forum topic id; null outside forum topics. |
| `media_kind` | string or null | See below. |
| `grouped_id` | int or null | Album id shared by messages of one album. |
| `urls` | array of strings | URLs in the message, in order, de-duplicated. |
| `forward` | object or null | Forward origin. |
| `link` | string or null | Permalink to the message. |

`topic_id` follows the rule from the forum plan: when the reply header has
`forum_topic` set, use `reply_to_top_id`, falling back to
`reply_to_msg_id`; otherwise null. Messages in a forum's General topic
carry no such header and get null.

`media_kind` is one of `photo`, `video`, `gif`, `sticker`, `voice`,
`audio`, `document`, `webpage`, `poll`, `other`, or null when the message
has no media. It is derived from Telethon's convenience properties in
that order of precedence (`gif` and `sticker` before `document`, since
both are documents underneath).

`urls` collects both plain URLs and the targets of text links
(`MessageEntityTextUrl`). Entity offsets are UTF-16 code units, so plain
URLs are read through Telethon's `get_entities_text()` rather than by
slicing the Python string.

`forward` is `{"from_id": int or null, "from_name": string or null,
"date": ISO-8601 string or null}`. `from_id` is a marked peer id, the
same form `group_id` uses.

`link`:

- public group or channel: `https://t.me/<username>/<id>`
- private supergroup or channel: `https://t.me/c/<bare id>/<id>`
- inside a forum topic: the topic id is inserted before the message id,
  for example `https://t.me/<username>/<topic_id>/<id>`
- basic (non-super) group: null, since Telegram has no permalinks there

`to_message` currently receives only the group id. It gains the resolved
entity's username so it can build `link`; the three existing callers pass
it through.

## 3. `tg get <group> <id>... [--pretty]`

Fetch specific messages by id. Output is a JSON array of `Message`
objects in the order the ids were given. Ids that do not exist are
omitted; the command still exits 0, and an empty array is a valid result.
At least one id is required.

Used to hydrate reply parents that fall outside a fetched range. `tg
thread` cannot do this: it walks down from a root, not up to a parent.

## 4. `tg download <group> <id>... --dir DIR [--max-bytes N]`

Download the photos attached to the given messages into `DIR`.

- `DIR` is created if missing, with mode 0700.
- Files are named `<group_id>_<message_id>.jpg`. An existing file is
  overwritten.
- For each photo the largest size variant not exceeding `--max-bytes`
  (default 5 MiB) is downloaded. Telegram stores several sizes per photo,
  so a tight budget yields a smaller picture rather than a failure.
- Only `media_kind == "photo"` is downloaded. Images sent as files are
  documents and are skipped.

Output is a JSON array with one object per requested id, in order:

```json
{"id": 123, "status": "saved", "path": "/abs/dir/-1001_123.jpg",
 "bytes": 48213, "reason": null}
```

`status` is `saved` or `skipped`. For skipped entries `path` and `bytes`
are null and `reason` is `not_found`, `not_photo` or `too_large`. Skips
do not make the command fail; it exits 0 whenever the group resolved and
the session is authorised.

This is the first command that writes anything. It writes only inside the
directory the caller names. The README security note changes from
"strictly read-only" to "read-only towards Telegram".

## Errors

No new error types. Existing mappings apply: `AuthError`,
`GroupNotFoundError`, `AmbiguousGroupError`, `FloodWaitError`,
`TelegramError`, and `UsageError` for the option conflicts above. An
unwritable `--dir` surfaces as a usage error naming the path.

## Testing

Tests first, in the repo's existing style: `SimpleNamespace` doubles for
Telethon objects, Click's `CliRunner`, `uv run pytest -q` and
`uv run ruff check src tests` green after every step.

Cases that must be covered:

- `--after-id` passes `min_id`, `max_id` and `reverse=True`, returns the
  oldest `--limit` messages, and leaves the default path untouched.
- Each option conflict yields a JSON usage error with exit 2.
- Every new field: populated, absent (null or empty array), and the
  precedence rules for `media_kind`.
- `urls` with a text link, a plain URL containing non-BMP characters
  before it, and a duplicate.
- `link` for public, private, forum-topic and basic-group cases.
- `get`: order preserved, missing ids omitted, all ids missing.
- `download`: saved, `not_found`, `not_photo`, `too_large`, variant
  selection under `--max-bytes`, directory creation.
- Existing JSON shape tests are extended, not replaced: old fields keep
  their names, types and order.

Live checks after implementation, against real groups:

- Page a known range with a small `--limit` and confirm the union equals
  a single large fetch of the same range.
- `link` values open the right message, including one inside a forum
  topic of `@sponsor_usa`.
- `tg download` saves a photo that opens.

## Documentation and release

- `README.md` and `skill/SKILL.md`: new options, commands, fields, the
  paging contract and the revised security note.
- Version bumped to 0.2.0 in `pyproject.toml` and `cli.py`.
- `docs/plans/20260419-forum-topics-support.md`: mark the `topic_id`
  field as delivered here.
- After merging: `uv tool install --reinstall ~/src/tg-cli` and
  `scripts/install-skill.sh` so the installed tool and skill match.
