# Changelog

## 0.6.0

Hardening release: correctness, transactional integrity, and operational
hardening on top of the epistemics/evidence work.

### Hardening

- **Evidence correctness**: symbol evidence is now identity + content (a
  normalized body hash). A symbol whose body materially changed is stale;
  moved-but-unchanged is fresh; legacy rows without `symbolHash` keep
  presence-only behaviour.
- **Atomic writes**: low-level DB helpers no longer commit; every semantic
  mutation (create/update/delete/relate/import/conflict resolve) runs in one
  transaction and rolls back on failure.
- **Validation**: `memory_update` reconstructs and revalidates the item; memory
  lifecycle transitions are enforced; the same schema invariants apply to
  create, update, and import.
- **Hard context budget**: `token_budget` is a hard upper bound; every section
  is capped and `engineering_context` returns `budget`, `estimatedTokens`, and
  per-section omitted counts.
- **Relation integrity**: `UNIQUE(from_id, to_id, kind)`, lookup indexes,
  self/dangling/cycle rejection, and idempotent duplicates.
- **Crash-safe migrations**: `BEGIN IMMEDIATE` lock, per-migration transaction,
  postcondition verification, and no schema-version stamp on failure.
- **Hook state**: atomic temp+fsync+rename, a private `0700` directory with
  `0600` files, and a TTL sweep.
- **Enforcement policy**: `TOTEM_ENFORCEMENT` = `off` | `warn` | `normal` |
  `strict` controls blocking (`strict` fails closed); fail-open events are
  logged to the hook state directory.
- **CI/release gates**: `.github/workflows/ci.yml` (pytest on 3.13/3.14, CLI +
  MCP startup smoke, build artifacts, npm pack); publishing now depends on CI.
- **Typed errors**: MCP tools return `{error: {code, message}}` with codes
  (NOT_FOUND, INVALID_ARGUMENT, SCHEMA_ERROR, CONFLICT, STALE, DB_UNAVAILABLE,
  MIGRATION_FAILED, INTERNAL).
- **Restorable imports**: `memory_import` gains `normal`/`strict`/`replace`
  modes, `--dry-run`, validation before mutation, and a complete report.
- **Resource limits**: caps on title/statement/details/tags/metadata/evidence,
  import size, and token budget.
- **Indexed hot paths**: a `statement_normalized` column and a locator table
  remove whole-corpus scans from density dedup and file registration.
- **Conflict relevance**: context only surfaces conflicts touching considered
  memories or critical classes.
- **Scope semantics + explainability**: task scope boosts only when it matches
  the current task; context returns a `why` map for each included memory.
- **Audit actors**: history records actor/session/source/commit/request_id;
  relations and conflict resolutions are recorded.
- **Idempotency**: writes accept an `operation_id`; a replay returns the stored
  result instead of re-executing.

### Memory model (epistemics)

- **Provenance**: `memory_create` takes `asserted_by` (user/test/source/git/doc/runtime/agent); it drives a default `confidence` when none is given (user 1.0 … agent 0.6, hypotheses capped at 0.4).
- **Semantic scope**: `user` / `project` / `path` / `task` (raw or JSON kind/value). Context groups USER CONTEXT → PROJECT CONTEXT and applies precedence in scoring; user items are no longer duplicated in the type sections. Items in the global user DB default to user scope.
- **Relations**: new `memory_relations` table plus `memory_relate` / `memory_relations` tools and `supersedes_id`. `supersedes` / `invalidates` update the target's status and leave context; `contradicts` surfaces in CONTEXT CONFLICTS.
- **Applicability**: `current` / `legacy` / `deprecated` / `planned`, orthogonal to status, shown in serialized context.
- **Evidence**: `register_file_read` keys on `(path, subject)`, so a file can hold several facts and the same fact updates in place instead of one blob per path. Evidence gains `symbol` and `blobHash`; staleness prefers symbol presence (moved code is not stale), then whole-file blob equality, then the line range; symbols are auto-guessed from the nearest def/class when omitted.
- **Density**: identical statements return the existing memory instead of duplicating; very short statements get a warning.
- Path activation includes one bounded relation hop; `engineering_context` accepts a `semantic_candidates` hook (extra candidate source; deterministic scoring still ranks).

### Context

- `get_open_conflicts()` feeds `engineering_context`, so a resolved conflict no longer keeps resurfacing; `get_all_conflicts` remains for audit/export.
- Potentially stale items are surfaced in a STALE KNOWLEDGE section (high-risk classes always shown, lower ones budget-truncated last) instead of silently vanishing; the staleness scan covers flagged items too; adds `staleIds`.

### Enforcement hooks

- Commit gates hold one pending entry per file (OpenCode plugin and Python hook); registering clears only its own path, so parallel reads each need registration. Legacy single-slot state migrates.
- Reading a file with an invariant/constraint tagged `verify:<file>` requires the registration to carry a verify tag before other tools unblock.

### Portability

- `memory_export` is now the archival format: `format_version`, `schema_version`, `items`, `conflicts`, `relations`. `memory_import` accepts older formats, imports relations and conflicts with dedup, and refuses newer formats.
- `memory_history` tool and `totem timeline <id>` expose the immutable history table for auditing why an agent believed something.

### Database

- Real schema versioning: a `meta` table with ordered migrations (SCHEMA_VERSION 4); legacy DBs are inferred from columns and stamped. No write when already current.
- `TOTEM_USER_DB` overrides the user memory DB path (hosts that keep data in a volume point it at a persistent path; tests isolate it to a temp dir).

## 0.5.1

### Installer (`bin/totem.js`)

- MCP server is registered as `uvx totem-mcp==<npm package version>` (pinned to the release) and pre-warmed during `npx totem`, so agent startups use the cached uvx environment and never hit the network. Falls back to the `totem-mcp` PATH binary when uvx is unavailable or the pin cannot be resolved. Supersedes the 0.5.0 "PATH binary so local installs take effect immediately" behavior for released versions.
- Installer now upgrades stale installs: compares `totem --version` against the package version and runs `pipx upgrade` / `uv tool upgrade` / `pip install --upgrade` on mismatch (previously it only checked presence, so installs never moved off old versions).
- Registers the totem MCP server in Kimi Code's user-level `~/.kimi-code/mcp.json` so totem is available in every project (project-level `.mcp.json` still overrides).
- Wires the enforcement hooks into Kimi Code's `~/.kimi-code/config.toml` (`PreToolUse`/`PostToolUse`/`UserPromptSubmit` → `totem-hook.py pre|post|clear`). Idempotent, TOML-safe append; strips legacy per-script totem hook entries.
- Fixed: crash with a raw stack trace when the CLI was installed but its bin dir was not on PATH (`execSync("totem --version")` was uncaught); now prints a clear error and exits 1.

### Enforcement hooks

- Memory gates cost ONE `totem search` subprocess per tool call: terms are batched into a single FTS5 `OR` query capped at 5, instead of one subprocess per word (a miss spawned one cold Python process per word, which flooded the process table under parallel tool calls).
- Non-blocking per-session `flock` on all hooks: concurrent invocations fail open instead of stacking subprocesses.
- Legacy `totem-enforce.py`/`totem-store-read.py`: same batching/lock fixes applied.

## 0.5.0

### Enforcement plugin (rewritten)

- **Unified hook script** `hooks/totem-hook.py` (`pre`/`post`/`clear` subcommands) replaces the three divergent `totem-enforce.py` / `totem-store-read.py` / `totem-clear-state.py` copies for Claude Code and Kimi Code.
- **Commit-gates**: after a file read, all non-totem tools are blocked until `register_file_read_tool` is called; after edit/write, until `register_file_write_tool` is called. The old auto-created stub memories ("Agent read X") are gone; the agent records what it learned itself.
- **Read gate** now matches only `implementation` memories for the exact file path, and allows the retry after the agent checks memory.
- **OpenCode plugin rewritten**: named export, per-session state keyed by `sessionID` (cleared per-session on `session.idle`), gates on MCP tools (`totem_register_file_*`), no gate arming on failed tool calls.
- Fixed: `totem search` invocations passed `--project` after the subcommand (it is a group-level option), so every memory check errored and failed open, so gates never triggered.
- Fixed: shell injection in the OpenCode plugin (`execSync` with interpolated agent-controlled queries → `execFileSync` with arg arrays).
- Fixed: installer used non-existent `uvx --install` (now `uv tool install`), mangled JSONC configs containing `https://` URLs while stripping comments, never replaced stale Claude hook entries, and installed the Kimi plugin manifest pointing at non-existent hook paths.
- Fixed: removed self-dependency on `@emiliano-go/totem@^0.4.3`; Kimi manifest no longer references a missing `skills/` dir.
- Known limitations: OpenCode does not pass MCP tool arguments to `tool.execute.before`, so commit-gates clear on any register call regardless of path argument; OpenCode before-hook does not fire inside task subagents; Kimi plugin hooks do not fire in `kimi -p` print mode.

### MCP server

- **Fixed critical schema bug**: positional `SELECT *` reads misaligned columns on fresh databases (`scope` is mid-table in `CREATE_TABLE` but appended at the end on migrated DBs). Every second `register_file_read_tool` / `register_file_write_tool` call crashed with `the JSON object must be str, bytes or bytearray, not NoneType`. All reads now use explicit column lists.
- **Fixed FTS parse crashes**: queries containing `:`, unbalanced quotes, parens, or trailing operators (e.g. the documented `cmd:` tag workflow) no longer raise `FTS parse error`; queries are sanitized with an escaped fallback.
- **Fixed `engineering_context`** crashing entirely when the user-level DB (`~/.local/share/totem/totem.db`) has an older schema; it now migrates or degrades gracefully.
- **Fixed silent data loss** in `register_file_read`/`register_file_write` update path: new `tags` (and explicit `title`) were dropped on update; metadata line ranges now store the clamped values matching evidence.
- **Fixed `memory_import`** aborting the whole import when an ID existed only as a soft-deleted row; per-item failures no longer kill the batch.
- `omittedIds` in `engineering_context` output now actually lists budget-truncated items; `db_connection` no longer leaks connections on setup failure; conflicts reads use explicit columns; dead `expand_tags` removed.

### Install/layout changes

- MCP server is launched as the `totem-mcp` binary (pipx/uv tool install) instead of `uvx totem-mcp`, so locally installed versions take effect immediately.
