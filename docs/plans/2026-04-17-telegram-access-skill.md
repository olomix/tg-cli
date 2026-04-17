# Telegram Access CLI and Claude Code Skill

## Overview

Build `tg-cli`, a Python CLI wrapping Telethon (MTProto), so Claude Code can
read the user's Telegram groups on their behalf and generate digests or
answer search queries. Bundled with a Claude Code skill (`SKILL.md`) that
documents the CLI and instructs Claude when and how to use it.

**Problem solved:** The Telegram Bot API cannot read group history — only
messages addressed to a bot. MTProto (user-account API) is the only way
to search past group conversations. This project provides a minimal,
Claude-friendly wrapper so the model can list groups, fetch recent
messages, search full-text within a group, and pull reply threads.

**Integration:**
- CLI lives in `~/src/tg-cli`, installed as a standalone Python tool
  (via `uv tool install .` or `pipx install .`) exposing the `tg` command.
- Skill lives in `~/.claude/skills/telegram/SKILL.md` (installed via a
  symlink or install script from the repo's `skill/` directory).
- Credentials and session stored under `~/.config/tg-cli/`.

## Context (from discovery)

- **Environment:** macOS (Darwin 25.4.0), zsh, Python 3.12+ assumed
  (Telethon supports 3.8+).
- **Skill directory:** `~/.claude/skills/` already contains `gitlab-ci`,
  `pdf`, `task-status`. Pattern: `SKILL.md` at skill root with
  frontmatter (`name`, `description`), plus optional `scripts/` and
  `references/`. New skill follows the same structure.
- **ralphex CLI:** installed (v0.27.2) — can execute this plan.
- **No prior Telegram tooling** in the user's environment.

**Key external dependencies:**
- `Telethon>=1.36` — MTProto client.
- `click` or `typer` — CLI framework (plan uses `click` for stability).
- `pytest` + `pytest-asyncio` — testing.
- `pydantic` (optional, for JSON output schemas) — or stdlib dataclasses.

**Telegram API credentials:** user must obtain `api_id` + `api_hash`
from https://my.telegram.org/apps (personal developer credentials,
free). This is a one-time manual step documented in README.

## Development Approach

- **Testing approach:** Regular (code first, then tests) — the CLI is
  mostly a glue layer over Telethon, so tests are written after each
  command is wired up. Tests use Telethon client mocks via `unittest.mock`
  / `pytest` fixtures; no live Telegram calls in the test suite.
- Complete each task fully before moving to the next.
- Make small, focused changes.
- **CRITICAL: every task MUST include new/updated tests** for code
  changes in that task.
  - write unit tests for new functions/methods
  - write unit tests for modified functions/methods
  - cover both success and error scenarios
- **CRITICAL: all tests must pass before starting next task** — no
  exceptions.
- **CRITICAL: update this plan file when scope changes during
  implementation.**
- Run `pytest` after each change.
- Maintain backward compatibility within CLI command signatures once
  published.

## Testing Strategy

- **Unit tests:** required for every task (see Development Approach).
  - Mock `TelegramClient` for command tests — fixture returns canned
    dialog/message objects.
  - Test time-string parsing, group-name resolution, JSON output shape
    as pure functions (no mocks needed).
- **Integration smoke test (manual, not in CI):** a `tests/manual/`
  script that runs against a real Telegram account in a throwaway
  group — documented in README but not part of `pytest` default run.
- **No e2e UI tests** — this is a CLI-only project.

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered tasks with ➕ prefix.
- Document issues/blockers with ⚠️ prefix.
- Update plan if implementation deviates from original scope.
- Keep plan in sync with actual work done.

## What Goes Where

- **Implementation Steps** (`[ ]` checkboxes): code, tests, docs inside
  the `~/src/tg-cli` repo and the skill file.
- **Post-Completion** (no checkboxes): one-time manual setup the user
  performs (api_id registration, `tg login`, installing the skill link),
  and verification against a real Telegram account.

## Implementation Steps

### Task 1: Bootstrap project structure

- [x] create `pyproject.toml` with project name `tg-cli`, entry point
  `tg = "tg_cli.cli:main"`, deps: `telethon`, `click`, `python-dateutil`
- [x] create `src/tg_cli/` package layout (`__init__.py`, `__main__.py`,
  `cli.py`, `config.py`, `client.py`, `models.py`, `commands/__init__.py`)
- [x] add `README.md` stub (full content written in final task)
- [x] add `.gitignore` (Python defaults + `*.session`, `*.session-journal`,
  `.venv/`)
- [x] initialise git repo, first commit
- [x] create `tests/` with `conftest.py` and empty `__init__.py`
- [x] write a trivial test (`test_cli.py::test_entry_point_exists`) and
  run `uv run pytest` — must pass before task 2

### Task 2: Config loader and login command

- [x] implement `config.py`: load `~/.config/tg-cli/config.toml` with
  `api_id` (int) and `api_hash` (str); support `TG_CLI_CONFIG_DIR`
  env var override; raise clear error with setup instructions when
  missing
- [x] implement `client.py`: factory that returns an initialised
  `TelegramClient` using session file at
  `{config_dir}/session.session`; handle absent session cleanly
- [x] implement `commands/login.py`: `tg login [--phone X]` flow —
  interactive prompt for phone, code, and 2FA password if required;
  persist session file
- [x] wire `login` into `cli.py` command group
- [x] write tests for config loader (success, missing file, malformed
  toml, env var override)
- [x] write tests for login command flow using mocked
  `TelegramClient.start` — verify prompt order and error paths
- [x] run `pytest` — must pass before task 3

### Task 3: `tg groups` command (list dialogs)

- [ ] define `models.Group` dataclass (`id: int, title: str, type: str,
  username: str|None, member_count: int|None`) with `to_dict()` for
  JSON serialisation
- [ ] implement `commands/groups.py`: `tg groups [--type group|channel|all]
  [--limit N]` — iterates `client.iter_dialogs()`, filters by type,
  prints JSON list to stdout
- [ ] add `--pretty` flag for indented JSON output
- [ ] wire into `cli.py`
- [ ] write tests with mocked `iter_dialogs` returning varied dialog
  types — verify JSON shape, filtering, limit behaviour
- [ ] write tests for error path when not logged in (no session)
- [ ] run `pytest` — must pass before task 4

### Task 4: `tg messages` command (fetch recent messages)

- [ ] implement time-string parser in `commands/_time.py`: accept
  `24h`, `7d`, `2026-04-15`, `2026-04-15T10:00` — return UTC `datetime`
- [ ] implement group resolver in `commands/_resolve.py`: accept
  numeric id, `@username`, or title (case-insensitive substring match
  against `iter_dialogs` result); raise on ambiguous match
- [ ] define `models.Message` dataclass (`id, date, sender_id,
  sender_name, text, reply_to_id, group_id`) with `to_dict()`
- [ ] implement `commands/messages.py`: `tg messages <group> [--since
  <time>] [--limit N] [--pretty]` — uses `iter_messages(chat,
  offset_date, limit, reverse=True)`; prints JSON array
- [ ] wire into `cli.py`
- [ ] write tests for `_time.parse()` covering all accepted formats and
  invalid input
- [ ] write tests for `_resolve.resolve()` covering id/username/title
  paths and ambiguity error
- [ ] write tests for `messages` command with mocked client
- [ ] run `pytest` — must pass before task 5

### Task 5: `tg search` command (full-text search in a group)

- [ ] implement `commands/search.py`: `tg search <group> <query>
  [--since <time>] [--limit N] [--pretty]` — uses
  `iter_messages(chat, search=query, offset_date, limit)`; reuses
  resolver and time parser from task 4; outputs same `Message` JSON
  shape
- [ ] wire into `cli.py`
- [ ] write tests for search command with mocked client — verify
  query/since passed through, output shape matches `messages`
- [ ] write test for empty-result case (empty JSON array, exit 0)
- [ ] run `pytest` — must pass before task 6

### Task 6: `tg thread` command (fetch replies to a message)

- [ ] implement `commands/thread.py`: `tg thread <group> <message_id>
  [--limit N] [--pretty]` — uses `iter_messages(chat,
  reply_to=message_id, limit)`; includes the root message first, then
  replies
- [ ] wire into `cli.py`
- [ ] write tests for thread command with mocked client — verify root
  message is first, replies follow in chronological order
- [ ] write test for missing/invalid message id (non-zero exit, error
  JSON to stderr)
- [ ] run `pytest` — must pass before task 7

### Task 7: Error handling and UX polish

- [ ] standardise error output: all errors print
  `{"error": "...", "type": "..."}` to stderr, exit non-zero
- [ ] handle Telethon `FloodWaitError` with clear message (`retry
  after N seconds`)
- [ ] handle `SessionPasswordNeededError` (2FA) in login only; other
  commands should instruct user to run `tg login`
- [ ] add `--json-errors` / default behaviour so Claude can parse
  failures programmatically
- [ ] write tests for error output shape across all commands (parametrised)
- [ ] run `pytest` — must pass before task 8

### Task 8: Create the Claude Code skill

- [ ] create `skill/SKILL.md` in the repo with frontmatter
  (`name: telegram`, `description: ...`) instructing Claude when to use
  the tool (search user's Telegram groups, generate digests) and
  documenting each command's JSON output shape with example calls
- [ ] document in `SKILL.md`: one-time setup (api_id/api_hash,
  `tg login`), common workflows (daily digest: `tg groups` →
  `tg messages <group> --since 24h`), and that the skill is the source
  of truth for command names (not Claude's memory)
- [ ] create `scripts/install-skill.sh` that symlinks `skill/` to
  `~/.claude/skills/telegram/` (idempotent; checks for existing link)
- [ ] write tests for `install-skill.sh` using a temp HOME (shellcheck
  clean; verify symlink created, re-runs noop, errors if target is a
  non-symlink file)
- [ ] run `pytest` and `shellcheck scripts/install-skill.sh` — must
  pass before task 9

### Task 9: Verify acceptance criteria

- [ ] verify all commands from Overview are implemented: `groups`,
  `messages`, `search`, `thread`, `login`
- [ ] verify JSON output is parseable (feed every command output
  through `json.loads` in a test)
- [ ] verify `--pretty` works on every output command
- [ ] run full test suite (`uv run pytest -v`) — 100% pass
- [ ] run linter (`uv run ruff check src/ tests/`) — zero issues
- [ ] verify test coverage with `uv run pytest --cov=tg_cli` — target
  ≥80% on `src/tg_cli/`
- [ ] verify `shellcheck scripts/install-skill.sh` — clean

### Task 10: [Final] Documentation

- [ ] write `README.md` covering: what the tool does, install steps
  (`uv tool install .`), obtaining `api_id`/`api_hash` from
  my.telegram.org, `tg login`, each command with examples, skill
  installation via `scripts/install-skill.sh`, troubleshooting section
  (flood wait, re-login, session corruption)
- [ ] add short `skill/SKILL.md` cross-reference to README
- [ ] update any project knowledge docs in `docs/` if new patterns
  discovered during implementation

*Note: ralphex automatically moves completed plans to
`docs/plans/completed/`*

## Technical Details

### Project layout (target)

```
~/src/tg-cli/
  pyproject.toml
  README.md
  .gitignore
  src/tg_cli/
    __init__.py
    __main__.py          # python -m tg_cli
    cli.py               # click group, wires commands
    config.py            # config.toml + env var loader
    client.py            # TelegramClient factory
    models.py            # Group, Message dataclasses
    commands/
      __init__.py
      _time.py           # time-string parser
      _resolve.py        # group resolver (id / @username / title)
      login.py
      groups.py
      messages.py
      search.py
      thread.py
  tests/
    __init__.py
    conftest.py          # fake TelegramClient fixture
    test_config.py
    test_client.py
    test_time.py
    test_resolve.py
    test_login.py
    test_groups.py
    test_messages.py
    test_search.py
    test_thread.py
    test_errors.py
    manual/
      smoke_test.py      # not collected by pytest; runs live
  scripts/
    install-skill.sh
  skill/
    SKILL.md
  docs/
    plans/
      2026-04-17-telegram-access-skill.md  # this file
```

### Config file format (`~/.config/tg-cli/config.toml`)

```toml
api_id = 1234567
api_hash = "abcdef0123456789abcdef0123456789"
# optional:
# default_limit = 100
```

### JSON output shapes (stable contract)

**`tg groups` → list of:**
```json
{
  "id": -1001234567890,
  "title": "My Dev Group",
  "type": "supergroup",
  "username": "mydevgroup",
  "member_count": 42
}
```

**`tg messages` / `tg search` / `tg thread` → list of:**
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

**Errors (stderr) → single object:**
```json
{"error": "not logged in; run `tg login`", "type": "AuthError"}
```

### CLI surface

```
tg login [--phone PHONE]
tg groups [--type {group,channel,all}] [--limit N] [--pretty]
tg messages <group> [--since TIME] [--limit N] [--pretty]
tg search <group> <query> [--since TIME] [--limit N] [--pretty]
tg thread <group> <message_id> [--limit N] [--pretty]
```

`<group>` accepts: numeric id, `@username`, or a case-insensitive
substring of the title (error on ambiguous match).

`TIME` accepts: `24h`, `7d`, `2026-04-15`, `2026-04-15T10:00`.

### Processing flow (typical digest request)

1. Claude invokes `tg groups --type group` → parses JSON, picks target.
2. Claude invokes `tg messages <group> --since 24h` → parses JSON.
3. Claude synthesises digest from message array and returns to user.
4. For "what did X say about Y?" queries: `tg search <group> "Y"` then
   optionally `tg thread <group> <id>` for context.

### Rate limit strategy

Telethon handles `FloodWaitError` automatically for most flows. For
user-initiated commands, catch and surface as a clear error with
wait-time suggestion rather than silently blocking.

## Post-Completion

*Items requiring manual intervention or external systems — no
checkboxes, informational only.*

**Manual one-time setup by user:**
- Visit https://my.telegram.org/apps and register an application to
  obtain `api_id` and `api_hash` (personal, free, one-time).
- Create `~/.config/tg-cli/config.toml` with those credentials.
- Run `tg login` interactively once (phone code + optional 2FA
  password). Session persists in `~/.config/tg-cli/session.session`.
- Run `scripts/install-skill.sh` to link the skill into
  `~/.claude/skills/telegram/`.
- Restart Claude Code session so it picks up the new skill.

**Manual verification against real Telegram:**
- `tg groups | jq '.[] | .title'` — sanity check group list.
- `tg messages "<some group>" --since 24h | jq length` — confirm
  messages fetched.
- `tg search "<some group>" "test"` — confirm search works.
- Ask Claude for a digest of one of the groups — end-to-end validation
  of the skill.

**Operational notes:**
- Session file at `~/.config/tg-cli/session.session` grants full
  access to the user's Telegram account — treat as a credential.
  Document this prominently in README.
- If session is invalidated (password change, security event), user
  reruns `tg login`.
- Telegram may rate-limit heavy pulls; add `--limit` to keep requests
  bounded.
