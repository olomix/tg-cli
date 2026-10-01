# Incremental fetch, richer messages, get-by-id and photo download

## Overview

Make `tg` usable as the data source for an unattended digest job that
reads every message posted since its previous run, links back to source
messages, and looks at photos.

Four additions, all backward compatible:

1. **`tg messages --after-id N [--through-id M]`** — an id range read
   oldest-first, so a caller can page through everything after a stored
   cursor without losing messages on a busy day.
2. **Seven new fields on the Message JSON** — `sender_username`,
   `topic_id`, `media_kind`, `grouped_id`, `urls`, `forward`, `link`.
3. **`tg get <group> <id>...`** — fetch specific messages by id.
4. **`tg download <group> <id>... --dir DIR [--max-bytes N]`** — save
   photos to a local directory.

Existing commands keep their output and semantics when the new options
are not used; old JSON fields keep their names, types and order, and the
new ones are appended.

Design: `docs/superpowers/specs/2026-10-01-incremental-fetch-and-media-design.md`.
The spec is the source of truth for behaviour; this plan is the order of
work.

## Context (from discovery)

- **Project**: tg-cli, Python 3.10+, Telethon 1.43.1, Click. CLI only.
  Baseline on `main`: 235 tests pass, `ruff` clean.
- **Source files involved**:
  - `src/tg_cli/models.py` — `Message` dataclass and `to_dict()`.
  - `src/tg_cli/commands/_message.py` — `to_message(raw, group_id)`,
    shared by `messages`, `search` and `thread`.
  - `src/tg_cli/commands/_peer.py` — peer helpers; already tells
    channels from basic chats (`_CHANNEL_TYPES`, `_looks_like_channel`).
  - `src/tg_cli/commands/messages.py` — gains the range options.
  - `src/tg_cli/commands/search.py`, `thread.py` — callers of
    `to_message`, updated for links.
  - `src/tg_cli/errors.py` — gains `DownloadError`.
  - `src/tg_cli/cli.py` — registers `get` and `download`; version.
  - Create: `src/tg_cli/commands/get.py`, `src/tg_cli/commands/download.py`.
- **Test files**: `tests/test_messages.py`, `tests/test_search.py`,
  `tests/test_thread.py`, `tests/test_peer.py`, `tests/test_errors.py`,
  `tests/test_cli.py`, `tests/test_acceptance.py`; new
  `tests/test_message_fields.py`, `tests/test_get.py`,
  `tests/test_download.py`.
- **Patterns to follow**: `SimpleNamespace` doubles for Telethon
  messages and entities, `_AsyncIter` for `iter_messages`, `CliRunner`
  through `cli.main`, `make_client` patched per command module,
  `@handle_errors` for the JSON error contract, relative imports.
- **Telethon facts confirmed against the installed 1.43.1**:
  - In reverse mode `iter_messages` starts after `min_id` and stops at
    the first id at or above `max_id`; both bounds are exclusive.
  - `Message.photo` also returns a link preview's image and the picture
    of a "chat photo changed" service message, so it must not be used to
    decide `media_kind`.
  - Media wrappers: `MessageMediaPhoto`, `MessageMediaDocument`,
    `MessageMediaWebPage`, `MessageMediaPoll`. Document kinds are told
    apart by `document.attributes`: `DocumentAttributeSticker`,
    `DocumentAttributeAnimated`, `DocumentAttributeVideo`,
    `DocumentAttributeAudio` (with `voice`).
  - `telethon.utils.get_inner_text(text, entities)` returns the text
    covered by each entity and handles UTF-16 offsets correctly. It is
    what `Message.get_entities_text()` uses, and unlike that method it
    works on the test doubles.
  - `MessageFwdHeader` has `from_id` (a peer), `from_name`, `date`.
  - Photo size variants that hold a real image: `PhotoSize(size)`,
    `PhotoSizeProgressive(sizes)`, `PhotoCachedSize(bytes)`. The others
    are placeholders and are never downloaded: `PhotoStrippedSize`
    (blurred preview), `PhotoPathSize` (outline), `PhotoSizeEmpty`.
  - `client.download_media(message, file=path, thumb=<type string>)`
    downloads one chosen variant and returns the path it wrote. For a
    `path` that is an existing file this is that same path
    (`_get_proper_filename` leaves a valid existing path alone). The
    variant must be named by its `.type` string, not passed as an
    object (see `tg download` below).
  - In reverse mode Telethon starts the request at `min_id + 1`, so
    `min_id=2**31 - 1` overflows the 32-bit field and raises
    `struct.error`. `max_id` is only a local stop condition there and
    is not serialised, so passing `max_id=2**31` (from
    `--through-id 2**31 - 1`) is safe in itself. The overflow comes
    back one step later: after yielding id `2**31 - 1` Telethon asks
    for the next page from `2**31`. The range loop therefore stops on
    its own after its last id (see `tg messages` range options).
  - `client.get_messages(entity, ids=[...])` usually answers in request
    order with `None` for an id that does not exist, but that is not
    guaranteed: `_IDsIter` copies what Telegram sends, and Telegram may
    leave an invalid id out altogether, which makes the answer shorter
    than the request. Answers are matched to ids by `message.id`.
- **Exact-shape assertions**: `test_messages.py` (two tests),
  `test_search.py` and `test_thread.py` (one each) compare a whole
  message dict. The three contract tests use a sender with
  `username="alice"` and an entity
  `SimpleNamespace(id=1234567890, megagroup=True)`, so their expected
  dicts change again when `sender_username` (Task 2) and `link`
  (Task 5) are wired in. Each of those tasks lists the test files it
  must update. They are extended, not replaced.
- **Acceptance fixture**: `_fake_client_for` in
  `tests/test_acceptance.py` makes `get_messages` return a single
  message, which suits `thread`. `get` and `download` need a list, so
  the fixture needs a per-command branch.
- **Version string** lives in `pyproject.toml`, `src/tg_cli/__init__.py`
  and `src/tg_cli/cli.py`, and is asserted in `tests/test_cli.py`.

## Development Approach

- **testing approach**: TDD (tests first), as in the earlier plans.
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code
  changes in that task, covering success and error scenarios
- **CRITICAL: all tests must pass before starting next task**
- **CRITICAL: update this plan file when scope changes during
  implementation**
- after each task run `uv run pytest -q` and
  `uv run ruff check src tests`
- maintain backward compatibility: no existing field, option or exit
  code changes meaning

## Testing Strategy

- **unit tests**: required for every task. Telethon media, entity and
  forward objects are small plain classes; tests build real ones
  (`types.MessageMediaWebPage(...)`, `types.MessageEntityUrl(...)`) and
  hang them on `SimpleNamespace` messages, so `isinstance` checks in the
  code are exercised for real.
- **acceptance tests**: `tests/test_acceptance.py` is extended to cover
  the new commands and the full message shape through the public CLI.
- **no e2e/UI tests**: CLI-only project. Live checks against real groups
  are listed under Post-Completion.

## Progress Tracking

- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope
- keep plan in sync with actual work done

## Solution Overview

**Fields before commands.** Tasks 1 to 5 extend the message shape while
no command changes behaviour; every task leaves the suite green. Tasks 6
to 9 add the range options and the two commands on top of the finished
shape. `download` is split in two: Task 8 covers what to download and
where, Task 9 the defensive write path.

**New fields default to "absent".** Every new `Message` field has a
default (`None`, or an empty list for `urls`), so existing constructions
keep working and each extraction can land in its own task.

**Field extraction stays in `_message.py`** as small private helpers
called from `to_message`: one per field group, each testable through the
observable `Message`.

**Links are built from a precomputed base.** The link prefix depends
only on the group, so it is computed once per command from the resolved
entity by a new `_peer.py` helper, `message_link_base(entity)`, and
passed to `to_message`. This covers what the spec says `to_message`
needs (username, bare id, channel or basic group) without widening its
signature by three arguments. The helper belongs in `_peer.py` because
all four message-producing commands use it and it reuses the existing
channel detection there.

**`media_kind` is decided by `isinstance` on the outer media object**,
then on document attributes. No Telethon convenience property is used.

**`download` writes defensively**: temporary name in the target
directory, size check after download, `os.replace` into place.

## Technical Details

### `Message` (final shape)

```python
@dataclass(frozen=True)
class Message:
    id: int
    date: datetime
    sender_id: int | None
    sender_name: str | None
    text: str
    reply_to_id: int | None
    group_id: int
    sender_username: str | None = None
    topic_id: int | None = None
    media_kind: str | None = None
    grouped_id: int | None = None
    urls: list[str] = field(default_factory=list)
    forward: dict[str, Any] | None = None
    link: str | None = None
```

`to_dict()` emits the seven old keys in their current order, then the
new ones in the order above.

### Extraction rules

| Field | Source |
|---|---|
| `sender_username` | `raw.sender.username`, else the first active entry of `raw.sender.usernames`, else null (the rule `message_link_base` uses for groups) |
| `topic_id` | reply header with `forum_topic` true: `reply_to_top_id`, falling back to `reply_to_msg_id` (test `is not None`, not truthiness); else null |
| `grouped_id` | `raw.grouped_id`, else null |
| `media_kind` | see below |
| `urls` | `MessageEntityTextUrl.url`, and for `MessageEntityUrl` the covered text from `telethon.utils.get_inner_text(raw.message, raw.entities)`; order of appearance, duplicates removed. Use `raw.message`, the raw text the entity offsets refer to, never `raw.text`, which Telethon re-renders as markdown |
| `forward` | `raw.fwd_from` → `{"from_id", "from_name", "date"}`; `from_id` through `telethon.utils.get_peer_id` (marked id) or null; `date` ISO-8601 or null |
| `link` | `link_base` + optional `/<topic_id>` + `/<id>`; null when `link_base` is null |

`media_kind`, first match wins:

1. `raw.media` is null → null
2. `MessageMediaWebPage` → `webpage`
3. `MessageMediaPhoto` → `photo`
4. `MessageMediaDocument` → by `document.attributes`:
   `DocumentAttributeSticker` → `sticker`; `DocumentAttributeAnimated` →
   `gif`; `DocumentAttributeVideo` → `video`; `DocumentAttributeAudio`
   with `voice` → `voice`, otherwise `audio`; none of these → `document`
5. `MessageMediaPoll` → `poll`
6. anything else → `other`

Real media can arrive with an empty payload: an expired self-destructing
photo has `MessageMediaPhoto(photo=None)`, and a document can be `None`
or `DocumentEmpty`. Read attributes as
`getattr(document, "attributes", None) or []`, so such a message becomes
`document` instead of raising and aborting the whole batch.

A topic-creation service message carries no `forum_topic` reply header,
so it gets `topic_id` null and a link without the topic. That is
accepted and noted in the README.

### `message_link_base(entity) -> str | None`

- entity has a public username → `https://t.me/<username>`. Use
  `entity.username`; if that is empty, the first active entry of
  `entity.usernames`.
- channel or supergroup without one → `https://t.me/c/<entity.id>`,
  using the entity's own bare id.
- basic group → null.

### `tg messages` range options

- `--after-id` and `--through-id`:
  `click.IntRange(min=0, max=2**31 - 1)`. Telegram message ids are
  32-bit; a larger value makes Telethon raise `struct.error`, which is
  not an RPC error and would surface as a traceback.
- `--after-id` with `--since` → `click.UsageError`.
- `--through-id` without `--after-id` → `click.UsageError`.
- The range path is chosen with `after_id is not None`, not truthiness,
  so `--after-id 0` takes it.
- `--through-id` at or below `--after-id` → empty array. The session is
  still checked and the group still resolved first, so a bad group
  reports `GroupNotFoundError` rather than `[]`; only `iter_messages` is
  skipped.
- `--after-id 2147483647` (the largest id) → empty array by the same
  short-circuit, with or without `--through-id`: no larger id exists,
  and calling Telethon would overflow (see Context). `--through-id
  2147483647` itself is valid and must not be rejected.
- Range path: `iter_messages(entity, limit=limit, min_id=after_id,
  reverse=True)`, plus `max_id=through_id + 1` when given. The result is
  already oldest-first; it is not reversed. The loop breaks after
  yielding `--through-id`, or 2147483647 without it, so Telethon is
  never asked for the page after the largest id (see Context).
- Default path (no `--after-id`): the current code, untouched.

### `tg get`

`client.get_messages(entity, ids=[...])` returns a list that holds
`None` for ids that do not exist and may leave ids out (see Context).
Index the answer by `message.id` (`messages_by_id` in `_message.py`)
and output the found messages in the order the ids were requested; an
id requested twice is output twice. Ids are
`click.IntRange(min=1, max=2**31 - 1)`, `nargs=-1, required=True`.

For `get` and `download`, a negative group id needs the `--` separator,
and after `--` every token is positional. Options must therefore come
first: `tg download --dir d -- -100123 5` works,
`tg download -- -100123 5 --dir d` does not.

### `tg download`

- Options: `--dir` (required), `--max-bytes`
  (`click.IntRange(min=1)`, default `5 * 1024 * 1024`). Message ids are
  `click.IntRange(min=1, max=2**31 - 1)`.
- Directory, checked before connecting to Telegram:
  `os.makedirs(dir, mode=0o700, exist_ok=True)`, then require
  `os.path.isdir(dir)` and `os.access(dir, os.W_OK | os.X_OK)`. Any
  failure is a `click.UsageError` naming the path, so an existing
  read-only directory is a usage error too, not a late download error.
- Fetch: `client.get_messages(entity, ids=[...])`, indexed by
  `message.id` as in `tg get`, so a file is never named after another
  message's id.
- Per requested id: no message with that id → `not_found`; `media_kind != "photo"`, or a
  `media.photo` that is not a `types.Photo` (expired or empty) →
  `not_photo`; otherwise choose the variant.
- Variant choice: consider only the three real variants, matched by
  `isinstance`, each with its declared byte size: `PhotoSize` (`size`),
  `PhotoSizeProgressive` (the largest of `sizes`), `PhotoCachedSize`
  (`len(bytes)`). Everything else is ignored, including
  `PhotoStrippedSize`, `PhotoPathSize`, `PhotoSizeEmpty`, a
  `PhotoSizeProgressive` with an empty `sizes` list and any type not
  known today, so an unexpected entry cannot raise. Pick the
  largest that fits `--max-bytes`. Real variants exist but none fits →
  `too_large`. No real variant at all → `not_photo`.
- Download call: pass the chosen variant as its **type string**,
  `thumb=chosen.type` (for example `"y"`). Telethon's thumb lookup
  ignores a `PhotoSizeProgressive` object and then downloads nothing,
  and the largest variant of a modern photo is usually progressive. The
  string form works for every size class. The message passed is a copy
  whose photo holds only the chosen variant: Telethon sorts every size
  before it picks one and raises `ValueError` on a
  `PhotoSizeProgressive` with an empty `sizes` list. The copy's
  `video_sizes` is cleared too: Telethon searches those as well and
  raises `AttributeError` on a `VideoSizeEmojiMarkup` or
  `VideoSizeStickerMarkup`, which has no `type`. The message Telegram
  returned is not changed.
- Temporary file: `tempfile.mkstemp(dir=DIR, prefix=".", suffix=".jpg")`,
  close the descriptor, pass that path as `file=`. Telethon writes to
  an existing file under the name it was given, so the temporary path
  is the only one to track; the value `download_media` returns is not
  used. A contract test runs Telethon's real photo download to pin
  this.
- After the download: a 0-byte temporary file (which is also what a
  `None` return leaves) is a failure (`DownloadError`), never `saved`.
  A file larger than `--max-bytes` is deleted and reported `too_large`;
  if it cannot be deleted that is a `DownloadError`, never a skip.
  Otherwise `os.replace` it to `<group_id>_<message_id>.jpg`;
  `os.replace` swaps a symlink at the target instead of writing through
  it.
- Errors: the whole per-file sequence (create, download, stat, delete
  or replace) sits in one `try`. `OSError` → new `DownloadError` with a
  non-zero exit; this includes `ConnectionError` and `TimeoutError`
  from a dropped connection, which are `OSError` subclasses. Telethon
  RPC errors propagate and already map to `TelegramError` or
  `FloodWaitError`. A `finally` removes the temporary file if it still
  exists, on every path including an interrupt. That removal is best
  effort: when it fails, the error that ended the download is the one
  reported.
- Result entry: `{"id", "status", "path", "bytes", "reason"}`; `path` is
  absolute.

## What Goes Where

- **Implementation Steps** (checkboxes): code, tests, documentation and
  version bump inside this repository.
- **Post-Completion** (no checkboxes): reinstalling the `tg` tool on
  this machine and live checks against real Telegram groups.

## Implementation Steps

### Task 1: Add the new fields to the `Message` model

**Files:**
- Modify: `src/tg_cli/models.py`
- Modify: `tests/test_messages.py`
- Modify: `tests/test_search.py`
- Modify: `tests/test_thread.py`

- [x] write failing tests in `tests/test_messages.py`: `to_dict()` of a `Message` built with only the old arguments emits the seven old keys in their current order followed by `sender_username`, `topic_id`, `media_kind`, `grouped_id`, `urls`, `forward`, `link`, with `None` for each except `urls`, which is `[]`
- [x] write failing test: a `Message` built with every new field set serialises each value unchanged, and two instances do not share one `urls` list
- [x] add the seven fields with defaults to `Message` and extend `to_dict()`
- [x] update the exact-shape assertions in `tests/test_messages.py` (two), `tests/test_search.py` and `tests/test_thread.py` to include the new keys with their default values
- [x] run `uv run pytest -q` and `uv run ruff check src tests` - must pass before task 2

### Task 2: Extract `sender_username`, `topic_id` and `grouped_id`

**Files:**
- Modify: `src/tg_cli/commands/_message.py`
- Create: `tests/test_message_fields.py`
- Modify: `tests/test_messages.py`
- Modify: `tests/test_search.py`
- Modify: `tests/test_thread.py`

- [x] create `tests/test_message_fields.py` with a `_raw(**overrides)` helper building a minimal `SimpleNamespace` message, and call `to_message` directly
- [x] write failing tests for `sender_username`: sender with a username, sender without one, no sender
- [x] change the expected `sender_username` to `"alice"` in the three contract tests (`tests/test_messages.py`, `tests/test_search.py`, `tests/test_thread.py`), whose sender double already has that username; they fail until the extraction lands
- [x] write failing tests for `topic_id`: no reply header; `forum_topic` false; `forum_topic` true with `reply_to_top_id=42`; true with only `reply_to_msg_id=42`; true with both set (top id wins); `reply_to_top_id=0` is kept and does not fall through
- [x] write failing tests for `grouped_id`: present, absent
- [x] implement the three extractions in `_message.py` as private helpers called from `to_message`
- [x] run `uv run pytest -q` and `uv run ruff check src tests` - must pass before task 3

### Task 3: Extract `media_kind`

**Files:**
- Modify: `src/tg_cli/commands/_message.py`
- Modify: `tests/test_message_fields.py`

- [x] write failing tests, one per value, using real Telethon media objects on the `media` attribute: `photo`, `webpage`, `poll`, `sticker`, `gif`, `video`, `voice`, `audio`, `document`, `other` (for example `MessageMediaGeo`), and null for no media
- [x] write failing tests for the traps: a `MessageMediaWebPage` whose page has a photo is `webpage`; a service message with a `MessageActionChatEditPhoto` action and no media is null; a document with both animated and video attributes is `gif`; a sticker document that also has a video attribute is `sticker`
- [x] write failing tests for empty payloads: `MessageMediaPhoto(photo=None)` is `photo` and does not raise; `MessageMediaDocument(document=None)` and a `DocumentEmpty` are `document` and do not raise
- [x] implement `_media_kind(raw)` in `_message.py` following the precedence in Technical Details, and call it from `to_message`
- [x] run `uv run pytest -q` and `uv run ruff check src tests` - must pass before task 4

### Task 4: Extract `urls` and `forward`

**Files:**
- Modify: `src/tg_cli/commands/_message.py`
- Modify: `tests/test_message_fields.py`

- [x] write failing tests for `urls`: no entities gives `[]`; a `MessageEntityTextUrl` yields its hidden target; a `MessageEntityUrl` yields the covered text; a plain URL preceded by an emoji outside the BMP is extracted intact; the same URL as text link and plain URL appears once; several URLs keep their order; other entity types are ignored; a message whose `text` differs from `message` (markdown re-rendering) still yields the URL from `message`
- [x] write failing tests for `forward`: no `fwd_from` gives null; a forward from a channel gives its marked `from_id` and ISO `date`; a forward with only `from_name` gives `from_id` null; a naive `date` is treated as UTC
- [x] implement `_urls(raw)` with `telethon.utils.get_inner_text` and `_forward(raw)` with `telethon.utils.get_peer_id`, and call both from `to_message`
- [x] run `uv run pytest -q` and `uv run ruff check src tests` - must pass before task 5

### Task 5: Build message permalinks

**Files:**
- Modify: `src/tg_cli/commands/_peer.py`
- Modify: `src/tg_cli/commands/_message.py`
- Modify: `src/tg_cli/commands/messages.py`
- Modify: `src/tg_cli/commands/search.py`
- Modify: `src/tg_cli/commands/thread.py`
- Modify: `tests/test_peer.py`
- Modify: `tests/test_message_fields.py`
- Modify: `tests/test_messages.py`
- Modify: `tests/test_search.py`
- Modify: `tests/test_thread.py`

- [x] write failing tests in `tests/test_peer.py` for `message_link_base`: public supergroup; public broadcast channel; entity with empty `username` and an active entry in `usernames`; private supergroup (uses the bare `entity.id`, not a marked id); basic group gives null
- [x] write failing tests in `tests/test_message_fields.py` for `link`: base plus id; base plus topic id plus id when `topic_id` is set; private base with a topic; null when no base is passed
- [x] implement `message_link_base(entity)` in `_peer.py`, reusing the existing channel detection
- [x] add a keyword argument `link_base: str | None = None` to `to_message` and build `link` from it
- [x] pass `message_link_base(entity)` to `to_message` in `messages.py`, `search.py` and `thread.py` (both call sites in `thread.py`)
- [x] write a test in `tests/test_messages.py` that `tg messages` on an entity with a username emits `link` values of the public shape
- [x] update the expected `link` in the three contract tests: their entity is a private supergroup with bare id `1234567890`, so each message gets `https://t.me/c/1234567890/<its id>` (`tests/test_messages.py`, `tests/test_search.py`, and the root and both replies in `tests/test_thread.py`)
- [x] run `uv run pytest -q` and `uv run ruff check src tests` - must pass before task 6

### Task 6: Add the id range to `tg messages`

**Files:**
- Modify: `src/tg_cli/commands/messages.py`
- Modify: `tests/test_messages.py`

- [x] write failing tests: `--after-id 100` calls `iter_messages` with `min_id=100`, `reverse=True` and no `max_id`, and the output keeps the iterator's order without reversing; `--after-id 100 --through-id 200` adds `max_id=201`; `--limit` is passed through
- [x] write failing tests for boundaries using a fake `iter_messages` that applies `min_id`/`max_id` to messages at ids N, N+1, M, M+1: only N+1 through M are returned; also ids with gaps and a result of exactly `--limit` messages. The call-argument assertions above are the ones that pin the Telethon mapping; this fake only checks the `M + 1` arithmetic end to end
- [x] write failing test: `--after-id 0` calls `iter_messages` with `min_id=0` and `reverse=True` (it must not fall back to the newest-first path)
- [x] write failing tests for usage errors, each asserting the JSON error on stderr and exit code 2: `--after-id` with `--since`; `--through-id` alone; a negative id; a non-integer id; an id of `2147483648`
- [x] write failing tests: `--through-id` at or below `--after-id` prints `[]`, exits 0 and does not call `iter_messages`; the same options with an unknown group still report `GroupNotFoundError`
- [x] write failing tests for the top of the id range: `--after-id 2147483647` prints `[]`, exits 0 and does not call `iter_messages`; `--after-id 100 --through-id 2147483647` is accepted and calls `iter_messages` with `max_id=2147483648`
- [x] add the two options and the range path to `messages.py`, leaving the default path as it is
- [x] confirm the existing default-path tests pass unchanged (newest `--limit`, reversed; `--since` cutoff)
- [x] run `uv run pytest -q` and `uv run ruff check src tests` - must pass before task 7

### Task 7: Add `tg get`

**Files:**
- Create: `src/tg_cli/commands/get.py`
- Modify: `src/tg_cli/cli.py`
- Create: `tests/test_get.py`

- [x] write failing tests in `tests/test_get.py`: several ids return `Message` objects in the order requested, with `link` populated; ids that come back as `None` are omitted; all ids missing prints `[]` with exit 0; `--pretty` indents; a negative group id works in the form `get -- -1001234567890 5 6`
- [x] write failing tests for errors: no ids is a JSON usage error with exit 2; an id of 0, a non-integer and `2147483648` are usage errors; an unauthorised session gives `AuthError`; an unknown group gives `GroupNotFoundError`
- [x] create `src/tg_cli/commands/get.py`: `tg get <group> <id>... [--pretty]` using `make_client`, `resolve`, `client.get_messages(entity, ids=[...])` and `to_message`
- [x] register `get` in `src/tg_cli/cli.py`
- [x] run `uv run pytest -q` and `uv run ruff check src tests` - must pass before task 8

### Task 8: Add `tg download` — selection, directory and results

**Files:**
- Create: `src/tg_cli/commands/download.py`
- Modify: `src/tg_cli/errors.py`
- Modify: `src/tg_cli/cli.py`
- Create: `tests/test_download.py`
- Modify: `tests/test_errors.py`

- [x] write failing tests in `tests/test_errors.py`: `DownloadError` is mapped by `handle_errors` to `{"type": "DownloadError", ...}` with a non-zero exit
- [x] write failing tests in `tests/test_download.py` for results, using `tmp_path` and a fake `download_media` that writes bytes to the path it is given and returns it: a photo is saved as `<group_id>_<message_id>.jpg` with `status` `saved`, an absolute `path` and the real `bytes`; a missing id gives `not_found`; a text message, a link preview, a document and a `MessageMediaPhoto(photo=None)` give `not_photo`; entries come back in request order; a negative group id works in the form `download --dir d -- -1001234567890 5`
- [x] write failing tests for variant choice: among several sizes the largest that fits `--max-bytes` is chosen; a `PhotoSizeProgressive` counts by its largest entry; a `PhotoCachedSize` counts by the length of its bytes and can be chosen; `PhotoStrippedSize`, `PhotoPathSize` and `PhotoSizeEmpty` are never chosen and do not raise; a photo whose sizes are only placeholders gives `not_photo`; real variants that all exceed the limit give `too_large`; the default limit is 5 MiB
- [x] write failing tests for the `thumb` argument: it is the chosen variant's `.type` **string**, including when the chosen variant is a `PhotoSizeProgressive`; and passing that same value to the real `telethon.client.downloads.DownloadMethods._get_thumb` with the photo's sizes returns the chosen variant, not `None`
- [x] write failing tests for the directory: a missing `--dir` is created with mode 0700; a `--dir` that cannot be created, and an existing directory made read-only with `chmod 0o500`, are each a JSON usage error naming the path with exit 2, raised before `make_client` is called; `--dir` omitted is a usage error; an id of 0 and `2147483648` are usage errors
- [x] add `DownloadError` to `src/tg_cli/errors.py` and its branch in `_classify`
- [x] create `src/tg_cli/commands/download.py` with option parsing, the directory check, message fetch, per-message classification, variant choice and the straightforward save path, and register `download` in `src/tg_cli/cli.py`
- [x] run `uv run pytest -q` and `uv run ruff check src tests` - must pass before task 9

### Task 9: Harden the `tg download` write path

**Files:**
- Modify: `src/tg_cli/commands/download.py`
- Modify: `tests/test_download.py`

- [x] write failing tests for the temporary file: the path passed to `download_media` is inside `--dir` and ends in `.jpg`; after a successful save no temporary file is left in the directory
- [x] write failing tests for a download that produced nothing: `download_media` returning `None`, and one leaving a 0-byte file, each give a `DownloadError` JSON error with a non-zero exit, never `saved`
- [x] write failing test: a download that turns out larger than `--max-bytes` is deleted and reported `too_large`
- [x] write failing tests for failures mid-download, each asserting no file under the final name, no leftover temporary file, and that a file saved earlier in the same run stays: an `OSError` and a `ConnectionError` give `DownloadError`; a Telethon `RPCError` gives `TelegramError`; all exit non-zero
- [x] write failing tests for the target name: a symlink already there is replaced and the file it pointed to is unchanged; an existing regular file is overwritten
- [x] implement the write path from Technical Details: `mkstemp` with the `.jpg` suffix, use of the returned path, the empty and oversize checks, `os.replace`, one `try` around the per-file sequence with `except OSError` and a `finally` that removes the temporary file (the returned path is no longer used; see Task 13)
- [x] run `uv run pytest -q` and `uv run ruff check src tests` - must pass before task 10

### Task 10: Bump the version and extend the acceptance tests

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/tg_cli/__init__.py`
- Modify: `src/tg_cli/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_acceptance.py`
- Modify: `tests/test_errors.py`

- [x] change the expected version in `tests/test_cli.py` to `0.2.0` and watch it fail
- [x] ➕ add a test in `tests/test_cli.py` that `tg_cli.__version__` and `pyproject.toml` carry `0.2.0` too, since `tg --version` only reads `cli.py`
- [x] set the version to `0.2.0` in `pyproject.toml`, `src/tg_cli/__init__.py` and the `version_option` in `src/tg_cli/cli.py`
- [x] give `_fake_client_for` in `tests/test_acceptance.py` a per-command branch so `get_messages` returns a list for `get` and `download` and a single message for `thread`
- [x] extend `tests/test_acceptance.py`: `get` and `download` are registered and listed in `tg --help`; `messages`, `search`, `thread` and `get` each emit a message with exactly the fourteen documented keys
- [x] add an acceptance test that pages a mocked range: three calls of `tg messages --after-id C --through-id H --limit 2` over five messages return every message once, in order, and the last page is short
- [x] add an acceptance test: `tg download` over a mix of a photo, a text message and a missing id exits 0 with one entry per id
- [x] add `get` and `download` to the parametrised `module, argv` error tests in `tests/test_errors.py` (config, auth, flood wait), next to the existing commands
- [x] run `uv run pytest -q` and `uv run ruff check src tests` - must pass before task 11

### Task 11: Verify acceptance criteria

- [x] verify each numbered section of the spec against the code: range rules, the seven fields, `get`, `download`, errors
- [x] verify backward compatibility: `tg messages`, `tg search` and `tg thread` without new options behave as before; the first seven keys of every message are unchanged in name, type and order (the 235 tests from `main` pass against this code once the seven appended keys are hidden; only the version assertion differs)
- [x] verify every case listed in the spec's Testing section has a test
- [x] ➕ add the tests the verification found missing: `link` on the `--after-id` path of `tg messages` (dropping it there failed no test), a negative message id for `get` and `download`, and `AmbiguousGroupError` for `get` and `download`
- [x] run the full suite: `uv run pytest -q` (427 passed at this point; 458 after the review fixes in Task 13)
- [x] run lint: `uv run ruff check src tests`
- [x] run coverage: `uv run pytest --cov=tg_cli --cov-report=term-missing` - `_message.py`, `get.py`, `download.py` and the range path in `messages.py` at 90% or above (`_message.py` 99%, `get.py` 100%, `download.py` 100%, `messages.py` 97% with every range-path line covered)

### Task 12: [Final] Update documentation

**Files:**
- Modify: `README.md`
- Modify: `skill/SKILL.md`
- Modify: `docs/plans/20260419-forum-topics-support.md`

- [x] `README.md`: document `--after-id` and `--through-id` on `tg messages` with the paging contract (read `H`, page until a short page, checks a caller should make); add `tg get` and `tg download` sections with examples and the download result shape; replace the Message JSON shape with the fourteen-field version and describe each new field, including the `media_kind` values and the four `link` shapes
- [x] `README.md`: change the security note from "strictly read-only" to read-only towards Telegram, with `tg download` writing only inside the directory it is given; add `DownloadError` to the error types
- [x] `README.md`: extend the "Negative numeric ids" note for `get` and `download`: options go before `--`, with `tg download --dir photos -- -1001234567890 5` as the example; note that a topic-creation service message has `topic_id` null
- [x] `skill/SKILL.md`: mirror the README changes; add `get` and `download` to the command list, the new fields to the message shape, `DownloadError` to the known error types, and a short "read everything since last time" workflow using the paging contract; revise the "read-only" wording in "When to Use"
- [x] `docs/plans/20260419-forum-topics-support.md`: note at the top that the `topic_id` field (its Tasks 1 and 2, without the `Topic` dataclass) was delivered by this plan, and mark those items accordingly
- [x] move this plan to `docs/plans/completed/` (deferred - the executor moves the plan after the review phases)

### ➕ Task 13: Review fixes

Found by the peer and phase reviews after Task 12; each fix has a test
that was watched failing first, or, for a test of behaviour that was
already right, failing against a mutated copy of the code.

- [x] ➕ stop the range loop of `tg messages` at its last id, so Telethon is not asked for the page after id 2147483647 (f036d99)
- [x] ➕ fail with `DownloadError` instead of reporting `too_large` when an oversize file cannot be deleted (f036d99)
- [x] ➕ clarify in `README.md`, `skill/SKILL.md` and the spec that an empty page ends an id range, that only a non-null `grouped_id` within one group marks an album, and that an oversize download is `too_large` (59a023f, 58045a8)
- [x] ➕ name the temporary file a failed download could not delete in the error, through exception notes appended by `handle_errors` (58045a8). Withdrawn by the next review as too much machinery for a double fault: the notes hook is gone and that cleanup is best effort (485d5aa)
- [x] ➕ match the answer of `get_messages` to the requested ids by message id in `tg get` and `tg download`, with tests for an id left out, another order, an id requested twice and an empty answer (3590022)
- [x] ➕ track only the temporary path in the download write path, drop the tests of a renamed download, and add a contract test over Telethon's real photo download, an interrupt test and a closed-descriptor test (485d5aa)
- [x] ➕ treat a `PhotoSizeProgressive` with no sizes as a placeholder, and read `sender_username` from `sender.usernames` when `sender.username` is empty (789705e)
- [x] ➕ add tests: a lone `--through-id 0`, a writable but non-searchable `--dir`, a forum reply header naming no message, the link of a topic id of 0, real `types.Message` and `types.MessageReplyHeader` objects through `to_message`, and the migrated-chat redirect of `tg get` and `tg download` (29ec564)
- [x] ➕ bring this plan, the spec, the forum topics plan and the stale docstrings in line with the code, and document upgrading to 0.2.0 in `README.md` and `skill/SKILL.md`
- [x] ➕ hand Telethon a copy of the message whose photo holds only the chosen variant, so a `PhotoSizeProgressive` with no sizes next to a real variant no longer raises inside Telethon; the regression runs Telethon's real `download_media`. Say in the spec that `too_large` after a download means more bytes than `--max-bytes`
- [x] ➕ clear `video_sizes` on the photo copy handed to Telethon, so a `VideoSizeEmojiMarkup` or `VideoSizeStickerMarkup` next to the chosen size no longer raises inside Telethon; the regression runs Telethon's real `download_media` for both
- [x] run `uv run pytest -q` (462 passed) and `uv run ruff check src tests`

## Post-Completion

*Items requiring manual intervention or external systems - no checkboxes, informational only*

**Install the new version on this machine:**

- `uv tool install --reinstall ~/src/tg-cli`, then `tg --version` should
  print 0.2.0.
- `~/.claude/skills/telegram` is a symlink to `skill/` in this repo, so
  the updated skill is picked up without reinstalling; running
  `scripts/install-skill.sh` confirms it.

**Live checks against real groups** (not part of the automated suite):

- Range paging: for `@radio_t_chat`, read the newest id `H` with
  `tg messages @radio_t_chat --limit 1`, pick a cursor `C` about 300
  messages back, page `--after-id C --through-id H --limit 100` until a
  short page, and confirm the union equals one fetch with
  `--limit 5000` over the same range.
- Fields: in the same output, check a photo post (`media_kind`
  `photo`), a post with a link preview (`webpage`), a forwarded post
  (`forward` set) and a post with a text link (`urls` holds the hidden
  target).
- Links: open a `link` from `@radio_t_chat` and one from inside a topic
  of `@sponsor_usa`; both must land on the right message. Confirm
  `topic_id` is set on the topic message.
- `tg get @radio_t_chat <id> <id>` returns those messages in order.
- `tg download @radio_t_chat <photo id> --dir /tmp/tg-photos` saves a
  file that opens as an image; a small `--max-bytes` yields a smaller
  variant or `too_large`.

**Consumer:**

- The digest script designed in
  `~/.local/share/tg-digest/2026-10-01-tg-digest-design.md` depends on
  this release and checks for version 0.2.0.
