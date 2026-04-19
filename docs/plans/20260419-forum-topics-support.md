# Forum Topics Support

## Overview

Some supergroups have **Topics (forum)** mode enabled — Telegram's feature that splits the group into multiple first-class conversation threads (each topic looks like its own chat inside the group). Today `tg` has no idea topics exist: `tg groups` lists the supergroup as a single entry, `tg messages` returns every message from every topic mixed together, and there's no way to see the list of topics or filter to one.

This plan adds three things:

1. **`tg topics <group>`** — new command that lists topics in a forum-enabled supergroup.
2. **`tg messages <group> --topic <id>`** — new flag that filters the message stream to a single topic using Telethon's `reply_to=<topic_id>` path (the same server-side mechanism discussion threads use).
3. **`topic_id` field added to the Message JSON** — so existing `tg messages` output is introspectable for forum groups even without `--topic`, and downstream tooling (the Claude skill, `jq`, etc.) can group messages by topic.

No breaking change to existing JSON shapes — `topic_id` is additive and is `null` for non-forum messages.

## Context (from discovery)

- **Project**: tg-cli, Python + Telethon MTProto wrapper; CLI-only.
- **Relevant source files**:
  - `src/tg_cli/commands/messages.py` — the `tg messages` Click command. Gains a `--topic` option.
  - `src/tg_cli/commands/_message.py` — `to_message()` helper that maps Telethon messages to the `Message` dataclass. Gains topic-id extraction.
  - `src/tg_cli/models.py` — `Message` dataclass; gains `topic_id: int | None` field + `to_dict()` plumbing. New `Topic` dataclass.
  - `src/tg_cli/commands/_resolve.py` — reused unchanged for group lookup.
  - `src/tg_cli/cli.py` — Click group; registers the new `tg topics` subcommand.
  - Create: `src/tg_cli/commands/topics.py` — the new `tg topics` command.
  - `src/tg_cli/errors.py` — may need one new error class (`NotAForumError`) or we reuse `GroupNotFoundError` / a `TelegramError` — see Solution Overview.
- **Test files**: `tests/test_messages.py`, `tests/test_acceptance.py`, `tests/test_models.py` (if it exists); new `tests/test_topics.py`.
- **Telethon API (confirmed against installed v1.43.1)**:
  - `from telethon.tl.functions.messages import GetForumTopicsRequest` — signature `(peer, offset_date, offset_id, offset_topic, limit, q=None)`; returns `telethon.tl.types.messages.ForumTopics(count, topics, messages, chats, users, pts, order_by_create_date)`.
  - `telethon.tl.types.ForumTopic` has `id`, `title`, `top_message`, `unread_count`, `pinned`, `closed`, `hidden`, `icon_emoji_id`, `date`, `from_id`, `icon_color`.
  - `telethon.tl.types.MessageReplyHeader` has `forum_topic: Optional[bool]`, `reply_to_msg_id: Optional[int]`, `reply_to_top_id: Optional[int]`. Per Telegram's protocol: when `forum_topic=True`, `reply_to_top_id` is the topic id; for the special topic-creation service message, the topic id equals the message's own `id`.
  - `client.iter_messages(entity, reply_to=<topic_id>)` filters to one topic (same call shape as discussion-thread fetch — Telegram reuses the reply-thread index).
  - `ForumTopicDeleted` type exists for holes in the list; `messages.ForumTopics.topics` may include them. Filter them out in our listing.
- **Entity flag**: a `Channel` (supergroup) carries `.forum: bool`. `True` means "Topics is on". Calling `GetForumTopicsRequest` on a non-forum entity raises Telethon `ChannelForumMissingError`. Calling `iter_messages(reply_to=...)` on a non-forum group raises the same `MsgIdInvalidError` path `tg thread` already handles.
- **Prior plan**: the migrated-basic-chats plan landed on 2026-04-18 and established the patterns this plan follows — `SimpleNamespace` test doubles, Click subcommand wiring, JSON error contract via `@handle_errors`, `_peer.py` as home of shared peer helpers.

## Development Approach

- **testing approach**: TDD (tests first).
- complete each task fully before moving to the next; every task includes tests.
- all tests must pass before starting next task.
- run `uv run pytest -v` and `uv run ruff check src tests` after each task.
- maintain backward compatibility: the new `topic_id` field is additive; non-forum messages emit `null`; existing commands' JSON shape is preserved otherwise.
- update this plan file when scope changes during implementation.

## Testing Strategy

- **Unit tests**: required for every task. Use `SimpleNamespace` doubles matching the patterns in `tests/test_messages.py`, `tests/test_resolve.py`, `tests/test_groups.py`.
- **No e2e / UI**: CLI-only project. `tests/test_acceptance.py` is the closest to integration and gets extended with end-to-end forum scenarios.
- **Edge cases to cover explicitly**:
  - Forum group with multiple topics, including `ForumTopicDeleted` entries — listing excludes deleted placeholders.
  - Non-forum group passed to `tg topics` — clear error, no stack trace.
  - `tg messages --topic N` on a non-forum group — clear error.
  - `--topic N` where N doesn't exist — Telethon raises `MsgIdInvalidError`; surface as a clean JSON error.
  - Message in a topic: `topic_id` populated correctly (from `reply_to_top_id` or, for the topic-creation service message, the message's own id).
  - Message in non-forum group: `topic_id` is `null` — existing contract preserved.
  - `--topic` combined with `--since` and `--limit` — both filters compose correctly.

## Progress Tracking

- mark completed items with `[x]` immediately when done.
- add newly discovered tasks with ➕ prefix.
- document blockers with ⚠️ prefix.
- keep plan in sync with actual work done.

## Solution Overview

Three small, independent additions:

### 1. `topic_id` extraction

Add a helper `topic_id_of(raw)` (in `commands/_message.py`, next to the existing `to_message`) that inspects the Telethon message's `reply_to` header and returns:

```python
def topic_id_of(raw) -> int | None:
    reply_to = getattr(raw, "reply_to", None)
    if reply_to is None:
        return None
    if not getattr(reply_to, "forum_topic", False):
        return None
    # ``reply_to_top_id`` is the topic id for any message inside a topic
    # (including replies); for the topic-creation service message itself
    # it is absent and Telegram encodes the topic id as ``reply_to_msg_id``.
    # Use explicit ``is not None`` (not ``or``) so a defensive ``0``
    # sentinel from a weird fixture doesn't fall through to the fallback.
    top = getattr(reply_to, "reply_to_top_id", None)
    if top is not None:
        return top
    return getattr(reply_to, "reply_to_msg_id", None)
```

Call it from `to_message` to populate a new `Message.topic_id: int | None` field. Extend `Message.to_dict()` to include `"topic_id"`.

### 2. `tg topics <group>`

New module `src/tg_cli/commands/topics.py`:

- Resolves the group via existing `resolve()` (picks up migrated-chat redirect for free).
- Checks `entity.forum` — if `False`, raises a dedicated `NotAForumError` (added to `errors.py`). Typed error so the JSON output is actionable (`{"type": "NotAForumError", "error": "..."}`).
- Calls `client(GetForumTopicsRequest(peer=entity, offset_date=None, offset_id=0, offset_topic=0, limit=100, q=None))`.
- Also wraps the RPC in `try/except ChannelForumMissingError` → `NotAForumError`, as a defensive fallback if the pre-flight `entity.forum` check is wrong (e.g. race where the flag was flipped off between resolve and list, or a test double that lacks the attribute entirely).
- Paginates automatically until server returns fewer than `limit` topics or `--limit` (new option, default unbounded) is reached. Pagination cursor: next page uses the **last returned topic**'s `id`, `top_message`, and `date` attributes directly (`ForumTopic.date` is always populated; no need to match against `resp.messages`).
- Filters out `ForumTopicDeleted` entries.
- Emits a JSON array of `Topic` dicts. Shape:
  ```json
  {
    "id": 12,
    "title": "General",
    "top_message": 5423,
    "unread_count": 0,
    "pinned": true,
    "closed": false,
    "hidden": false,
    "icon_emoji_id": null
  }
  ```

Registered in `src/tg_cli/cli.py` next to the existing subcommands. `--pretty` flag mirrors other commands.

### 3. `tg messages <group> --topic <id>`

Small edit to `commands/messages.py`:

- New Click option `--topic` (type `click.IntRange(min=1)`, optional). Telegram topic ids are strictly positive; letting Click reject `0` / negatives gives a clean usage error.
- When present, pass `reply_to=topic_id` to `client.iter_messages(entity, limit=limit, reply_to=topic)`. Telethon narrows the iterator server-side; no client-side filtering needed.
- Map Telethon's `MsgIdInvalidError` / `PeerIdInvalidError` / `TopicDeletedError` (when the topic id doesn't exist, was deleted, or the group isn't a forum) to the existing `MessageNotFoundError` with a message that explicitly mentions the topic — keeps the error contract honest instead of swallowing into a generic `TelegramError`.

No changes to `tg search` / `tg thread` in this plan. `tg search` doesn't take a `reply_to` on the server side (Telegram's full-text search is per-group, not per-topic); adding a `--topic` there would require client-side filtering and is a follow-up. `tg thread`'s non-forum fallback is a different conversation.

### Scope boundaries (deliberate)

- **No `tg search --topic`**. Telegram's search API does not natively filter by topic; implementing it would mean a client-side scan of search results against `topic_id`. Out of scope.
- **No topic creation/edit/close operations**. Read-only is a project-wide invariant (per `README.md` security note).
- **No automatic topic detection in `tg messages`**. We won't emit a warning when the group is a forum and the user didn't pass `--topic` — that's noise. The `topic_id` field on each message already makes the structure visible.

## Technical Details

### New `Topic` dataclass (`src/tg_cli/models.py`)

```python
@dataclass(frozen=True)
class Topic:
    id: int
    title: str
    top_message: int
    unread_count: int
    pinned: bool
    closed: bool
    hidden: bool
    icon_emoji_id: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

**Construction note:** `telethon.tl.types.ForumTopic` declares `pinned`, `closed`, `hidden` as `Optional[bool]` — Telegram sends them as absent/`None` when the flag is unset (not `False`). `asdict` would then emit JSON `null`, violating the "boolean-shaped" promise. Coerce at construction: `Topic(..., pinned=bool(t.pinned), closed=bool(t.closed), hidden=bool(t.hidden), ...)`. Similarly, `ForumTopic.title` can be empty when `title_missing=True`; fall back to `t.title or ""` so the field is always a string.

### `Message.topic_id` field

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
    topic_id: int | None = None    # NEW — defaulted so existing callers
                                   # that don't know about topics keep
                                   # working without modification.
```

`to_dict()` includes `"topic_id"` explicitly (next to `"reply_to_id"` for symmetry).

**Default is load-bearing**: without `= None`, every existing `Message(...)` construction in tests and production code would fail to instantiate until task 2 is also applied. The default is what makes this plan's task ordering safe (task 1 lands the field, task 2 wires the extraction).

### New `NotAForumError` (`src/tg_cli/errors.py`)

A `TelegramError` subclass so existing `@handle_errors` picks it up and emits `{"type": "NotAForumError", ...}`. Message text: `"group <id-or-title> does not have Topics enabled"`.

### Topic pagination

`GetForumTopicsRequest`'s pagination uses a triple offset `(offset_date, offset_id, offset_topic)` from the last item on the current page. `ForumTopic.date` is always populated (confirmed against the v1.43.1 signature), so the cursor advancement is straightforward:

```python
offset_date, offset_id, offset_topic = None, 0, 0
collected: list[Topic] = []
while True:
    resp = await client(GetForumTopicsRequest(
        peer=entity,
        offset_date=offset_date, offset_id=offset_id, offset_topic=offset_topic,
        limit=PAGE_SIZE,
    ))
    fresh = [t for t in resp.topics if not isinstance(t, ForumTopicDeleted)]
    collected.extend(fresh)
    if user_limit and len(collected) >= user_limit:
        collected = collected[:user_limit]
        break
    if len(resp.topics) < PAGE_SIZE:
        break
    last = resp.topics[-1]
    new_cursor = (last.date, last.top_message, last.id)
    if new_cursor == (offset_date, offset_id, offset_topic):
        # Defensive: server returned the same cursor twice. Stop rather
        # than loop forever. Should not happen on a well-behaved server.
        break
    offset_date, offset_id, offset_topic = new_cursor
```

Reading `last.date` directly avoids the "walk `resp.messages` looking for the message whose id matches `top_message`" dance; it's simpler and there's no "fall back to `None`" branch that could restart pagination and loop forever.

### Topic-id semantics cross-reference

| Message flavor | `reply_to.forum_topic` | `reply_to.reply_to_top_id` | `reply_to.reply_to_msg_id` | Our `topic_id` |
|---|---|---|---|---|
| Normal reply inside a topic | True | topic id | replied-msg id | `reply_to_top_id` |
| Direct post in a topic | True | topic id | topic id (= top) | `reply_to_top_id` |
| Topic-creation service msg | True | absent | topic id (= own id) | `reply_to_msg_id` |
| Message in non-forum group | False or header absent | — | — | `None` |
| Reply thread in a linked-discussion group | True | discussion-thread id | — | `reply_to_top_id` (harmless — discussion threads look like topics to us, which is fine) |

## What Goes Where

- **Implementation Steps** (checkboxes): all code changes, tests, docs.
- **Post-Completion** (no checkboxes): manual verification against the live "Uniting for Ukraine 🇺🇦🇺🇸" supergroup that prompted this plan.

## Implementation Steps

### Task 1: Add `Topic` dataclass and `Message.topic_id` field

**Files:**
- Modify: `src/tg_cli/models.py`
- Modify (or create): `tests/test_models.py` (check if exists; otherwise add coverage to `tests/test_messages.py`'s helpers)

- [ ] write failing tests for `Message.topic_id`:
      - default position in `to_dict()` emits `"topic_id"` key alongside `"reply_to_id"`
      - `topic_id=None` serializes as JSON `null`
      - `topic_id=42` serializes as the integer
- [ ] write failing tests for `Topic` dataclass:
      - `to_dict()` emits all 8 fields
      - `icon_emoji_id=None` serializes as JSON `null`
- [ ] add `topic_id: int | None` field to `Message` dataclass; update `to_dict()` to include `"topic_id"`
- [ ] add `Topic` dataclass with 8 fields and `to_dict()`
- [ ] run `uv run pytest tests/test_models.py -v` (or wherever the Model tests live) — must pass before task 2
- [ ] run `uv run pytest -v` to confirm no other tests broke (the existing `Message(...)` constructions will fail until task 2)

Note: existing `to_message()` callers instantiate `Message(...)` positionally or by keyword. Adding a new dataclass field without a default WILL break them until task 2 wires the extraction. Two options: (a) give `topic_id` a default of `None` in the dataclass, or (b) accept the transitional breakage and fix it in task 2. Prefer (a) — no transitional breakage, and `None` is the right default for messages without a topic anyway.

### Task 2: Extract `topic_id` in `to_message`

**Files:**
- Modify: `src/tg_cli/commands/_message.py`
- Modify: `tests/test_messages.py` (or wherever `to_message` is unit-tested — `tests/test_message.py` if it exists)

- [ ] write failing tests for a new `topic_id_of(raw)` helper (or inline the logic in `to_message` — implementer choice, tests target the observable `Message.topic_id` field):
      - raw message with no `reply_to` → `topic_id=None`
      - `reply_to` with `forum_topic=False` → `topic_id=None`
      - `reply_to` with `forum_topic=True, reply_to_top_id=42` → `topic_id=42`
      - `reply_to` with `forum_topic=True, reply_to_top_id=None, reply_to_msg_id=42` → `topic_id=42` (topic-root service msg case)
      - `reply_to` with `forum_topic=True` and BOTH `reply_to_top_id=50` and `reply_to_msg_id=42` → `topic_id=50` (prefer top id)
- [ ] implement the extraction in `_message.py` so `to_message(raw, group_id)` returns a `Message` with `topic_id` populated
- [ ] verify existing `to_message` tests still pass (they should — `topic_id` defaults to `None` for all their fixtures)
- [ ] run `uv run pytest -v` — must be fully green before task 3

### Task 3: `NotAForumError`

**Files:**
- Modify: `src/tg_cli/errors.py`
- Modify: `tests/test_errors.py`

- [ ] write failing tests:
      - `NotAForumError("msg")` is a `TelegramError` subclass
      - `NotAForumError` is handled by `@handle_errors` and emits `{"type": "NotAForumError", "error": "msg"}` with a non-zero exit
- [ ] add `NotAForumError(TelegramError)` class in `errors.py`
- [ ] run `uv run pytest tests/test_errors.py -v` — must pass before task 4

### Task 4: `tg topics` command

**Files:**
- Create: `src/tg_cli/commands/topics.py`
- Modify: `src/tg_cli/cli.py` (register subcommand)
- Create: `tests/test_topics.py`

- [ ] write failing tests in `tests/test_topics.py`:
      - forum supergroup (entity with `forum=True`) with 3 topics (one `ForumTopicDeleted`) → JSON emits 2 topics with correct fields, `ForumTopicDeleted` filtered out
      - non-forum group (entity with `forum=False`) → `NotAForumError` JSON error; non-zero exit (pre-flight check)
      - `ChannelForumMissingError` raised by the server → same `NotAForumError` JSON error (defensive post-flight mapping)
      - topic with `title_missing=True` / empty title → JSON `"title": ""` (not `null`, not crash)
      - topic flags `pinned=None`, `closed=None`, `hidden=None` from Telethon → JSON emits literal `false` (not `null`) for all three (boolean-shape contract)
      - topic with `top_message=0` (fresh/empty topic) → included in output, `top_message` is `0`
      - `--limit 1` applied to a 3-topic response returns exactly 1 topic
      - pagination: server returns a full page then a short page → collected list is concatenation of both (mock two sequential `__call__` invocations on the client)
      - pagination safety: cursor that doesn't advance bails out (fake server returns same page twice; we detect it and stop)
      - `--pretty` emits indented JSON
      - auth failure surfaces `AuthError`
- [ ] create `src/tg_cli/commands/topics.py`: Click command signature `tg topics <group> [--limit N] [--pretty]`; uses `make_client`, `resolve`, pre-flight `entity.forum` check, `GetForumTopicsRequest` paginated loop with `ChannelForumMissingError → NotAForumError` mapping, filters `ForumTopicDeleted`, outputs `Topic`s via `json.dumps`
- [ ] inline the forum-shape check at the single call site (`if not getattr(entity, "forum", False): raise NotAForumError(...)`). Do NOT add an `_is_forum_entity` helper to `_peer.py` — it would be a one-line wrapper with a single caller, exactly the kind of premature abstraction the prior plan's review flagged. Adding a helper there is only justified when multiple modules import the same shape check (as with `is_migrated_chat` / `is_group_entity`).
- [ ] register `topics` subcommand in `src/tg_cli/cli.py`
- [ ] run `uv run pytest tests/test_topics.py -v` — must pass
- [ ] run full suite `uv run pytest -v` — must pass before task 5

### Task 5: `tg messages --topic` filter

**Files:**
- Modify: `src/tg_cli/commands/messages.py`
- Modify: `tests/test_messages.py`

- [ ] write failing tests:
      - `tg messages <group> --topic 42` calls `iter_messages(entity, limit=..., reply_to=42)` — verify kwargs via mock
      - non-forum group + `--topic 42` → Telethon raises `MsgIdInvalidError` → surfaces as `MessageNotFoundError` JSON error whose text mentions the topic id
      - nonexistent topic id → same `MessageNotFoundError` path
      - deleted topic id (`TopicDeletedError`) → same `MessageNotFoundError` path
      - `--topic 0` and `--topic -5` → Click usage-error (non-zero exit, no stack trace — this is `IntRange(min=1)`)
      - `--topic` combined with `--since 7d` → both filters applied (`since` cutoff still respected inside the filtered stream)
      - no `--topic` flag → existing behavior unchanged (existing passing tests already cover this; confirm)
      - `topic_id` field in returned messages matches the requested `--topic` value (when Telethon fixtures carry the matching reply-to headers) — ties the new field to the new filter
- [ ] add `--topic` Click option (type `click.IntRange(min=1)`, optional) to `messages.py`
- [ ] pass `reply_to=topic` (when set) into `iter_messages`
- [ ] wrap the iteration in a `try/except (MsgIdInvalidError, PeerIdInvalidError, TopicDeletedError)` ONLY when `--topic` is set; raise `MessageNotFoundError` with a message that includes the topic id and the group reference
- [ ] run `uv run pytest tests/test_messages.py -v` — must pass
- [ ] run full suite — must pass before task 6

### Task 6: Acceptance test

**Files:**
- Modify: `tests/test_acceptance.py`

- [ ] add acceptance test: build a mocked client for a forum supergroup, run `tg topics <group>` and assert the JSON shape (2+ topics, correct fields, deleted entries absent)
- [ ] add acceptance test: run `tg messages <group> --topic <id>` against the same mocked client; assert returned messages all carry `topic_id == <id>`
- [ ] add acceptance test: run `tg messages <group>` (no `--topic`) against a mocked forum client that has messages from two topics; assert the flat output includes both topics' messages and each message's `topic_id` is populated correctly
- [ ] add acceptance test: run `tg topics <group>` against a non-forum group; assert `NotAForumError` JSON on stderr, non-zero exit
- [ ] run `uv run pytest tests/test_acceptance.py -v` — must pass before task 7

### Task 7: Verify acceptance criteria

- [ ] confirm: `tg topics <forum-group>` lists topics; filters deleted; paginates
- [ ] confirm: `tg topics <non-forum-group>` returns clean `NotAForumError`
- [ ] confirm: `tg messages <forum-group> --topic N` returns only that topic's messages
- [ ] confirm: `tg messages <forum-group>` (no `--topic`) returns the flat stream with `topic_id` populated per-message
- [ ] confirm: `tg messages <non-forum-group>` unchanged; `topic_id` is `null` on every message
- [ ] confirm: `--topic` composes with `--since` and `--limit`
- [ ] run full suite: `uv run pytest -v`
- [ ] run lint: `uv run ruff check src tests`
- [ ] coverage sanity: `uv run pytest --cov=tg_cli tests/ --cov-report=term-missing` — new modules should be ≥ 90%

### Task 8: Update documentation

**Files:**
- Modify: `README.md`
- Modify: `skill/SKILL.md`

- [ ] `README.md`: add `tg topics` to the Commands section between `tg groups` and `tg messages`; document `--topic` flag on `tg messages`; add one-paragraph explanation of Telegram Topics (supergroups with Forum mode enabled); note that `topic_id` is `null` for non-forum messages
- [ ] `README.md`: add a "Message JSON shape" note about the new `topic_id` field
- [ ] `skill/SKILL.md`: mirror the above — short "Topics" section explaining when Claude should call `tg topics`, how to filter with `--topic`, and how to interpret `topic_id` on messages; add `tg topics` to the command index
- [ ] `skill/SKILL.md`: add one-liner "For forum supergroups, always check `tg topics` before `tg messages` if the user refers to a channel-within-the-group by name"
- [ ] move this plan to `docs/plans/completed/20260419-forum-topics-support.md`

## Post-Completion

*Manual verification against the live account — not part of the automated suite.*

- Run `~/.local/bin/tg topics sponsor_usa --pretty` against the live "Uniting for Ukraine 🇺🇦🇺🇸" supergroup that prompted this plan. Expect a non-empty list with recognizable topic titles.
- Pick one topic id from that output and run `~/.local/bin/tg messages sponsor_usa --topic <id> --since 7d --limit 20`. Assert every returned message's `topic_id` equals the one you asked for.
- Run `~/.local/bin/tg topics <some-non-forum-group>`. Expect a clean `NotAForumError` JSON on stderr.
- Spot-check `tg messages <some-regular-group>`: `topic_id` must be `null` on every message; existing behavior preserved.
