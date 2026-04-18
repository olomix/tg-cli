# Handle Migrated Basic Chats

## Overview

`tg groups` currently lists legacy basic chats (`telethon.tl.types.Chat`) that have been migrated to supergroups. These appear as zombie entries with `member_count: 0` and no messages — passing their id to `tg messages`/`search`/`thread` returns an empty list, because the real messages live in the new supergroup (`Channel` with a `-100…` marked id).

This plan does two things:

1. **Filter migrated zombies out of `tg groups`** so the listing only shows dialogs that actually hold messages.
2. **Auto-follow `Chat.migrated_to` in the resolver** so old basic-chat ids (copy-pasted from previous listings, docs, or memory) transparently resolve to the new supergroup instead of silently returning nothing.

Together, the fixes make `tg`'s observable behavior consistent: every id the user sees in `tg groups` works; every id that *used to* work continues to work.

## Context (from discovery)

- **Files involved:**
  - `src/tg_cli/commands/groups.py` — `_dialog_to_group` / `_classify` decide what ends up in the `tg groups` output.
  - `src/tg_cli/commands/_resolve.py` — `resolve` / `_get_entity_or_not_found` decide which entity downstream commands (`messages`, `search`, `thread`) operate on.
  - `tests/test_groups.py`, `tests/test_resolve.py` — existing `SimpleNamespace`-based fixtures to extend.
- **Related patterns:** tests use `SimpleNamespace` doubles (`_small_group`, `_supergroup`) to avoid constructing real Telethon TL objects. New tests must match that style.
- **Telethon shape:** `telethon.tl.types.Chat` has a `migrated_to` attribute that is `None` for a live chat and an `InputChannel(channel_id=…, access_hash=…)` for a migrated one. `ChatForbidden` never has a migration pointer.
- **Marked-id helper** (`src/tg_cli/commands/_peer.py`) already knows how to emit the right marked id for `Channel`/`Chat`; reused unchanged.
- **Skill docs** (`skill/SKILL.md`) mention dialog listings and id usage and may need a one-line note.

## Development Approach

- **testing approach**: TDD (tests first)
- complete each task fully before moving to the next
- make small, focused changes
- **every task MUST include new/updated tests** for code changes in that task
- **all tests must pass before starting next task** — no exceptions
- run `uv run pytest -v` after each task
- maintain backward compatibility: non-migrated chats/supergroups/channels continue to behave exactly as today
- update this plan file when scope changes during implementation

## Testing Strategy

- **Unit tests**: required for every task. Use `SimpleNamespace` fixtures matching `tests/test_groups.py` / `tests/test_resolve.py` conventions.
- **No e2e / UI**: this project is CLI-only; there is no UI or Playwright suite. `tests/test_acceptance.py` is the closest to integration and should be extended if a user-visible JSON contract changes.
- **Edge cases to cover explicitly:**
  - `Chat` with `migrated_to=None` — must behave exactly as today (no filtering, no rewrite).
  - `Chat` with `migrated_to=InputChannel(...)` — filtered from `tg groups`; auto-resolved to the target `Channel` when looked up by id.
  - `ChatForbidden` (no `migrated_to` attribute) — behaves as today; neither path regresses.
  - Target channel resolution fails (e.g. `get_entity(InputChannel)` raises `ValueError` / access error) — resolver surfaces a clear `GroupNotFoundError` rather than a traceback.
- **Test doubles:** existing `_small_group` / `_supergroup` helpers are extended (or mirrored) to accept a `migrated_to` value.

## Progress Tracking

- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope

## Solution Overview

Three small changes gated behind one predicate:

```python
def _is_migrated_chat(entity) -> bool:
    """Return True for a basic Chat that has been migrated to a Channel."""
    return getattr(entity, "migrated_to", None) is not None
```

placed in `src/tg_cli/commands/_peer.py` (already the home of peer-shape helpers, so both `commands/groups.py` and `commands/_resolve.py` can import it without creating a new module).

- `commands/groups.py::_dialog_to_group` returns `None` when `_is_migrated_chat(entity)` is true → zombie filtered out of `tg groups` output.
- `commands/_resolve.py` gets a new module-private helper `_maybe_follow_migration(client, entity, reference)` that returns the migrated-to `Channel` (or the original entity when not migrated). **Both** numeric-id/`@handle` path (`_get_entity_or_not_found`) **and** title-substring path (`_resolve_by_title`) call it before returning — otherwise searching the zombie by its old title would silently break (the new supergroup may have a different title, or the user may only remember the legacy name).
- The follow-up `client.get_entity(entity.migrated_to)` is wrapped in a broadened error tuple — `ValueError` *plus* `telethon.errors.ChannelInvalidError` and `ChannelPrivateError` — since the embedded `access_hash` may be stale in the current session, and Telethon raises those specifically for hash/permission issues rather than a plain `ValueError`. On failure we raise `GroupNotFoundError` with a message that explicitly names the migration so the user can re-list dialogs.

**Scope boundaries (deliberate):**
- Only `Chat.migrated_to`. A `Chat` with `deactivated=True` but no migration pointer is a distinct tombstone state and is **out of scope** — those still appear in the listing. We can revisit if they turn out to be noisy.
- No infinite-recursion guard. Telethon `Channel`/`ChannelForbidden` never carry `migrated_to`, so one follow is always sufficient; a loop would only fire if Telegram itself returned a channel that re-points to another chat, which the protocol does not produce.

No new error classes, no new CLI flags, no change to JSON *shape* of `Group` or `Message` — but note the semantics change below.

### JSON semantics change (intentional)

When a caller passes the old basic-chat id to `tg messages|search|thread`, the `group_id` field on returned messages will be the **migrated-to supergroup's** marked id (`-100…`), not the id that was on the command line. This is the correct behavior (the message genuinely belongs to that supergroup), and it mirrors what `tg groups` will show after the filter lands. It must be documented explicitly in both `README.md` (`tg messages` section, not just troubleshooting) and `skill/SKILL.md` so Claude doesn't cache a stale mapping.

## Technical Details

### Migration detection

- Telethon's `Chat.migrated_to` is `None` by default. When set, it is a `telethon.tl.types.InputChannel` with `channel_id` + `access_hash`. `client.get_entity(InputChannel)` is Telethon's documented way to resolve it.
- `ChatForbidden` (no access to the chat) does not expose `migrated_to`; `getattr(..., "migrated_to", None)` handles that uniformly.
- `Channel` / `ChannelForbidden` entities never have `migrated_to`; the predicate returns `False` for them — safe to call unconditionally and loop-free.

### Listing filter (`tg groups`)

- `_dialog_to_group` already returns `None` for non-group dialogs; add a second short-circuit after the kind check:
  ```python
  if _is_migrated_chat(entity):
      return None
  ```
- Because the migrated-to supergroup is a separate dialog, it already appears in `iter_dialogs()` output; no information is lost.

### Resolver follow-through (`tg messages|search|thread`)

New helper in `_resolve.py`:

```python
async def _maybe_follow_migration(client, entity, reference):
    if not _is_migrated_chat(entity):
        return entity
    try:
        return await client.get_entity(entity.migrated_to)
    except _MIGRATION_FOLLOW_ERRORS as exc:
        raise GroupNotFoundError(
            f"group {reference!r} was migrated to a supergroup that "
            "could not be resolved (run `tg groups` to find its new id)"
        ) from exc
```

Where:

```python
_MIGRATION_FOLLOW_ERRORS = (
    ValueError,
    telethon.errors.ChannelInvalidError,
    telethon.errors.ChannelPrivateError,
)
```

Called from **both** resolver paths:

- `_get_entity_or_not_found` — right after `client.get_entity(key)` succeeds, before the `_is_group_entity` guard. The guard then runs against the migrated-to `Channel`, which passes.
- `_resolve_by_title` — right before returning `matches[0][1]`, so an old-title substring match also transparently redirects. (The migrated zombie may still show up in `iter_dialogs()` even after we filter it from the `tg groups` JSON listing, because the filter lives in `_dialog_to_group`, not in the iterator source.)

Callers that use `marked_peer_id(entity)` (like `messages.py`) automatically emit the new supergroup's `-100…` id in the JSON `group_id` field — intentional; see "JSON semantics change" above.

## What Goes Where

- **Implementation Steps** (checkboxes): predicate in `_peer.py`, two call-site changes, new/updated tests, docs touch-up.
- **Post-Completion** (no checkboxes): one-time manual verification against the real account to confirm the zombie group disappears and the new supergroup's messages come back through the old id.

## Implementation Steps

### Task 1: Add `_is_migrated_chat` predicate

**Files:**
- Modify: `src/tg_cli/commands/_peer.py`
- Modify: `tests/test_peer.py`

- [x] write failing unit tests in `tests/test_peer.py` for `_is_migrated_chat`:
      - returns `True` when entity has `migrated_to` set to a truthy value
      - returns `False` when `migrated_to` is `None`
      - returns `False` when `migrated_to` attribute is missing (e.g. `Channel`, `ChatForbidden` double)
- [x] add `_is_migrated_chat(entity) -> bool` helper in `src/tg_cli/commands/_peer.py` using `getattr(..., "migrated_to", None) is not None`
- [x] run `uv run pytest tests/test_peer.py -v` — must pass before task 2

### Task 2: Filter migrated chats from `tg groups`

**Files:**
- Modify: `src/tg_cli/commands/groups.py`
- Modify: `tests/test_groups.py`

- [x] extend `_small_group` test helper (or add sibling `_migrated_chat`) in `tests/test_groups.py` to accept a `migrated_to` value
- [x] write failing test: `_dialog_to_group` returns `None` for a `Chat` double whose `migrated_to` is an `InputChannel`-shaped value
- [x] write failing test: `tg groups` JSON output excludes the migrated chat when the dialog list contains both the zombie and the migrated-to supergroup
- [x] write regression test: a non-migrated `Chat` (`migrated_to=None`) still appears in the output unchanged
- [x] import `_is_migrated_chat` in `src/tg_cli/commands/groups.py`
- [x] in `_dialog_to_group`, after `_classify` returns, short-circuit to `None` when `_is_migrated_chat(entity)` is true
- [x] run `uv run pytest tests/test_groups.py -v` — must pass before task 3

### Task 3: Auto-follow migration in resolver (numeric-id & `@handle` paths)

**Files:**
- Modify: `src/tg_cli/commands/_resolve.py`
- Modify: `tests/test_resolve.py`

- [x] extend the `_client()` test fixture in `tests/test_resolve.py` so `get_entity` can accept a **list** of side-effects (enabling tests that need two sequential `get_entity` calls). Back-compat: when a single value is passed, wrap it in a one-item list.
- [x] write failing test: `resolve(client, "-584241293")` where the first `get_entity` returns a migrated `Chat` double and the second returns a `Channel` double — assert the returned entity is the `Channel` and that `get_entity` was awaited twice (second call with the `migrated_to` pointer)
- [x] write failing test: migrated-chat path where the second `get_entity` raises `ValueError` — resolver raises `GroupNotFoundError` with a message mentioning migration
- [x] write failing test: migrated-chat path where the second `get_entity` raises `telethon.errors.ChannelInvalidError` — same `GroupNotFoundError` surfaces (staleness case)
- [x] write failing test: migrated-chat path where the second `get_entity` raises `telethon.errors.ChannelPrivateError` — same `GroupNotFoundError` surfaces
- [x] write regression test: non-migrated numeric id resolves in exactly one `get_entity` call (no extra lookup)
- [x] write regression test: `@username` resolving to a live `Channel` is unaffected (no migration follow-up)
- [x] add `_is_migrated_chat` import and a new `_maybe_follow_migration(client, entity, reference)` async helper in `src/tg_cli/commands/_resolve.py`; define `_MIGRATION_FOLLOW_ERRORS = (ValueError, ChannelInvalidError, ChannelPrivateError)`
- [x] call `_maybe_follow_migration` from `_get_entity_or_not_found` immediately after the initial `client.get_entity` succeeds, before `_is_group_entity` runs
- [x] run `uv run pytest tests/test_resolve.py -v` — must pass before task 4

### Task 4: Auto-follow migration in resolver (title-substring path)

**Files:**
- Modify: `src/tg_cli/commands/_resolve.py`
- Modify: `tests/test_resolve.py`

- [x] write failing test: `resolve(client, "old group name")` where `iter_dialogs` yields a migrated `Chat` whose title matches — assert `_maybe_follow_migration` fires and the returned entity is the target `Channel`
- [x] write failing test: same as above but the second `get_entity` raises `ChannelInvalidError` — surfaces `GroupNotFoundError`
- [x] write regression test: title-substring match against a live `Channel` (no migration pointer) returns the channel without any `get_entity` call — guards against redundant lookups
- [x] in `_resolve_by_title`, before returning `matches[0][1]`, pass it through `_maybe_follow_migration(client, entity, reference=query)`
- [x] run `uv run pytest tests/test_resolve.py -v` — must pass before task 5

### Task 5: Acceptance test spanning listing + resolve

**Files:**
- Modify: `tests/test_acceptance.py`

- [x] add acceptance test that builds a mocked client returning a migrated `Chat` and its target `Channel` in `iter_dialogs`, runs `tg groups` and `tg messages -- -<old_chat_id>`, and asserts:
      - zombie chat is absent from `tg groups` JSON
      - `tg messages` returns messages from the target supergroup, with the migrated-to `group_id` (the `-100…` one, not the original `-<bare>` on the command line)
- [x] run `uv run pytest tests/test_acceptance.py -v` — must pass before task 6

### Task 6: Verify acceptance criteria

- [ ] confirm: `tg groups` no longer emits migrated zombies (member_count 0, hollow rows)
- [ ] confirm: passing an old basic-chat id to `tg messages`, `tg search`, `tg thread` returns messages from the migrated supergroup
- [ ] confirm: resolving a migrated chat by title substring also transparently redirects
- [ ] confirm: non-migrated dialogs (live basic chats, supergroups, channels, DMs filtered earlier) are unchanged
- [ ] run full suite: `uv run pytest -v`
- [ ] run lint: `uv run ruff check src tests`
- [ ] (optional) run `uv run pytest --cov=tg_cli` and eyeball coverage on the new branches

### Task 7: [Final] Update documentation

**Files:**
- Modify: `README.md`
- Modify: `skill/SKILL.md` (if it documents dialog listings or id usage)

- [ ] in `README.md`'s `tg messages` section (not just troubleshooting), add a short paragraph explaining that migrated basic chats are transparently redirected to their new supergroup, **and that the `group_id` field on returned messages reflects the resolved supergroup's `-100…` id rather than the id passed on the command line**
- [ ] add a matching paragraph to `skill/SKILL.md` wherever it documents `group_id` / dialog listings so the skill model does not cache a stale id mapping
- [ ] mention that the follow-through also fires for title-substring lookups (old zombie title still works)
- [ ] move this plan to `docs/plans/completed/20260418-handle-migrated-basic-chats.md`

## Post-Completion

*Items requiring manual verification against the live Telegram account — not part of the automated suite.*

**Manual verification:**
- Re-run `~/.local/bin/tg groups --type group --pretty | jq '.[] | select(.id == -584241293)'` against the reporting user's session and confirm the zombie is gone.
- Re-run `~/.local/bin/tg messages --since 7d -- -584241293` and confirm messages from the migrated supergroup are returned.
- Spot-check that a known-live basic chat (if the user has any) still shows up in `tg groups` and returns messages, to guard against over-filtering.

**External system updates:**
- None. No consumers, no deployment artefacts, no third-party services.
