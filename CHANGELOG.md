# Changelog

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
