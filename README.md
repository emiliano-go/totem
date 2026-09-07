# totem

<p align="center">
  <a href="https://pypi.org/project/totem-mcp/">
    <img src="https://img.shields.io/pypi/v/totem-mcp?logo=pypi&logoColor=white&style=for-the-badge&cacheSeconds=0" alt="PyPI">
  </a>
  <a href="https://pypi.org/project/totem-mcp/">
    <img src="https://img.shields.io/pypi/pyversions/totem-mcp?logo=python&logoColor=white&style=for-the-badge&cacheSeconds=0" alt="Python">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-10AC84?style=for-the-badge" alt="License">
  </a>
  <a href="https://modelcontextprotocol.io">
    <img src="https://img.shields.io/badge/MCP-Compatible-8A2BE2?logo=modelcontextprotocol&logoColor=white&style=for-the-badge" alt="MCP">
  </a>
</p>

Persistent memory layer for engineering agents. Store decisions, invariants, gotchas, and rejected ideas in a local Turso database with staleness detection, conflict detection, full-text search, and structured context assembly.

## Why

AI coding agents lose engineering context between sessions. They re-discover the same gotchas, re-debate the same decisions, and forget invariants that were already established. `totem` persists this knowledge locally and serves it back to agents as structured context, ordered by relevance.

## Features

- **Four memory types**: decision, invariant, gotcha, rejected_idea (each with type-specific metadata)
- **Staleness detection**: SHA256 content hashing on linked evidence; auto-transitions items to `potentially_stale` when source code changes
- **Conflict detection**: surfaces contradictory decisions or invariants on overlapping code ranges
- **Conflict resolution**: mark conflicts as resolved and pick a winner
- **Dedup on create**: warns if a memory with the same title already exists
- **Full-text search**: Turso FTS5 on title, statement, details, and tags
- **Hybrid memory**: project memories in `.totem/`, user memories in `~/.local/share/totem/`. Context assembly searches both.
- **Context assembly**: scored pipeline with `current_task` relevance boost, token budget support, section ordering per spec
- **Task resumption**: tag in-progress work with `task:<name>`, resume across sessions
- **Command outcomes**: store command results with `cmd:` tag prefix, check before re-running
- **Workspace scoping**: auto-detects git root for correct DB placement; explicit `--project` override available
- **Export/import**: move memories between machines or seed a new project from an existing one
- **Agent integration**: bundles AGENTS.md and SKILL.md for automatic agent instruction setup
- **MCP server**: 14 tools exposed via Model Context Protocol
- **CLI**: 14 commands for manual operations
- **Auto-init**: agent config installed automatically on first tool call

## Install

Requires Python 3.13+.

```bash
git clone https://github.com/emiliano-go/totem.git
cd totem

# With uv (recommended)
uv sync

# Or with pip
pip install .
```

This installs two entry points: `totem` (CLI) and `totem-mcp` (MCP server).

## Setup for your agent

Two steps: (1) add the MCP server, (2) run `totem init` once per project.

### Step 1: Add the MCP server

**Claude Code:**
```bash
claude mcp add totem -- uvx totem-mcp
```

**opencode** (add to `~/.config/opencode/opencode.json`):
```json
{
  "mcp": {
    "totem": {
      "type": "local",
      "command": ["uvx", "totem-mcp"],
      "enabled": true
    }
  }
}
```

**Claude Desktop / Cursor / Windsurf** (add to config):
```json
{
  "mcpServers": {
    "totem": {
      "command": "uvx",
      "args": ["totem-mcp"]
    }
  }
}
```

**VS Code** (add to `.vscode/mcp.json`):
```json
{
  "servers": {
    "totem": {
      "type": "stdio",
      "command": "uvx",
      "args": ["totem-mcp"]
    }
  }
}
```

**Project-scoped** (Claude Code / Cursor / opencode, checked into repo):

The included `.mcp.json` handles this automatically. Just open your project and the agent picks it up.

### Step 2: Use it

That's it. The first time you call any totem tool, it automatically:
- Creates `.totem/` in your project
- Copies AGENTS.md + SKILL.md to `~/.config/opencode/` (for opencode)
- Appends to `./AGENTS.md` in your project root (for Claude Code)

No manual init needed. The MCP server handles everything on first use.

If you prefer to set up manually:
```bash
totem init          # creates .totem/ + agent config
totem init --project /path/to/other  # for a different project
```

## Agent instructions

totem bundles two files that teach your agent how to use the memory system:

- **AGENTS.md**: Mandatory behavioral rules (check memories on session start, store discoveries mid-task, save learnings on completion)
- **SKILL.md**: Detailed workflow with code examples for each phase (discovery, mid-task, completion)

`totem init` installs these automatically. What it does per agent:

| Agent | AGENTS.md | SKILL.md |
|-------|-----------|----------|
| opencode | Appends to `~/.config/opencode/AGENTS.md` | Creates `~/.config/opencode/skills/totem/SKILL.md` |
| Claude Code | Appends to `./AGENTS.md` in project root | N/A (uses AGENTS.md only) |
| Claude Desktop / Cursor | Append to `./AGENTS.md` in project root | N/A |

AGENTS.md is append-safe: it checks for the `## totem Memory System` marker before writing, so your existing instructions are never overwritten.

### Tag conventions

- `task:<name>`: In-progress work. Query with `memory_tasks_tool` / `totem tasks`.
- `cmd:<command>`: Command outcomes. Query with `memory_commands_tool` / `totem commands`.

## Quick start

### MCP server

Start the server:

```bash
totem-mcp
```

Then use it from your agent. Example tool calls:

```
# Check what you were working on last session
memory_recent_tool(limit=5)

# Get full context for a task
engineering_context_tool(tags=["api", "database"], task="Refactor auth middleware",
  current_task="Adding JWT refresh endpoint")

# Store a decision
memory_create_tool(type="decision", title="Use FTS5 for search",
  statement="SQLite FTS5 is sufficient for our search needs",
  tags=["search", "sqlite"],
  metadata={"rationale": "No external dependency needed"})

# Store a command outcome
memory_create_tool(type="gotcha", title="uv pip install -e . works",
  statement="Editable install works with uv pip on PEP 668 systems",
  tags=["cmd:uv-pip-install", "python"])
```

### CLI

```bash
# Initialize totem in your project
totem init

# Create a memory item
totem create --type decision --title "Use FTS5 for search" \
  --statement "SQLite FTS5 is sufficient for our search needs" \
  --tags "search,sqlite" --metadata '{"rationale": "No external search dependency needed"}'

# Get it back
totem get <ITEM_ID>

# Search (full-text + tags)
totem search --query "FTS5 search" --tags "sqlite"

# List recent items
totem recent

# List in-progress tasks
totem tasks

# List command outcomes
totem commands

# Resolve a conflict
totem resolve <CONFLICT_ID> --resolution "Kept existing: Use FTS5"

# Assemble context with task relevance
totem context --tags "search,sqlite" --task "Add fuzzy search" \
  --current-task "Implementing search for product catalog" --budget 4096

# Export all memories to a file
totem export -o backup.json

# Import memories from a file
totem import backup.json
```

## MCP tools (14)

All tools return JSON strings. Every tool accepts an optional `project` parameter to override workspace scoping.

| Tool | Description |
|------|-------------|
| `totem_init_tool` | Initialize totem for a project (creates `.totem/` and DB) |
| `memory_create_tool` | Create a memory item. Warns if title already exists. |
| `memory_get_tool` | Retrieve by ID with staleness check |
| `memory_update_tool` | Update any field. Provide `reason` (strongly recommended). |
| `memory_delete_tool` | Soft-delete (requires `reason`) |
| `memory_list_tool` | Filtered listing with `sort` param (`created_at`, `updated_at`, `importance`) |
| `memory_recent_tool` | List most recently created memories (default limit 5) |
| `memory_tasks_tool` | List in-progress task memories (tagged `task:*`) |
| `memory_commands_tool` | List command outcomes (gotchas tagged `cmd:*`) |
| `memory_search_tool` | FTS5 full-text search (includes tags) with type/tag filters |
| `resolve_conflict_tool` | Mark a conflict as resolved with a resolution description |
| `engineering_context_tool` | Scored context assembly with `current_task` relevance boost |
| `memory_export_tool` | Export all memories and conflicts as portable JSON |
| `memory_import_tool` | Import memories from an export dict (skips duplicate IDs) |

## CLI commands (14)

All commands accept `--project <path>` to override workspace scoping.

| Command | Description |
|---------|-------------|
| `totem init` | Initialize totem and install agent instructions |
| `totem create` | Create a new memory item |
| `totem get <ID>` | Retrieve by ID (`--no-evidence` skips staleness check) |
| `totem update <ID>` | Update an item (`--reason` optional, defaults to "maintenance") |
| `totem delete <ID>` | Soft-delete (requires `--reason`) |
| `totem list` | List with `--sort` (`created_at`, `updated_at`, `importance`) and filters |
| `totem recent` | List most recently created memories (`--limit` default 5) |
| `totem tasks` | List in-progress task memories (tagged `task:*`) |
| `totem commands` | List command outcomes (gotchas tagged `cmd:*`) |
| `totem resolve <ID>` | Mark a conflict as resolved (`--resolution` required) |
| `totem search` | Full-text search (includes tags) with type/tag filters |
| `totem export` | Export all memories and conflicts as JSON (`-o` for file output) |
| `totem import <FILE>` | Import memories from a JSON export file |
| `totem context` | Assemble scored context for a task |

All commands output JSON to stdout.

## Workspace scoping

totem auto-detects your project root using `git rev-parse --show-toplevel`. The `.totem/totem.db` file is created relative to the git root, not your current working directory. This means the MCP server works correctly regardless of which subdirectory it starts in.

To override auto-detection, pass `--project <path>` on any CLI command or `project` parameter on any MCP tool.

## Memory types

Each type captures a different kind of engineering knowledge:

| Type | Purpose | Metadata |
|------|---------|----------|
| `decision` | A choice that was made | `rationale` (optional, but strongly recommended: explain WHY) |
| `invariant` | A rule that must hold | `verificationMethod`, `condition` (required) |
| `gotcha` | A non-obvious pitfall discovered | (none) |
| `rejected_idea` | A proposal that was considered and declined | `proposal`, `reasonRejected` (required) |

## Hybrid memory

totem stores memories in two locations:

- **Project memories**: `.totem/totem.db` (in your repo, checked into version control or gitignored)
- **User memories**: `~/.local/share/totem/totem.db` (personal preferences, global patterns)

`engineering_context` searches both databases, with project memories taking precedence. This means your agent remembers project-specific decisions and your personal coding preferences across all projects.

## Export and import

Move memories between machines or seed a new project:

```bash
# Export everything from the current project
totem export -o project-memories.json

# Import into a different project
cd /path/to/other-project
totem import ../project-memories.json
```

Import skips items with duplicate IDs and reports counts of imported vs skipped items.

## Context assembly

The `engineering_context` tool runs a scored pipeline across both project and user memories:

**Scoring formula:**
```
score = 0.3*tag_match + 0.25*importance + 0.15*confidence + 0.1*recency + 0.2*task_similarity
```

Invariants get a 1.25x multiplier. Potentially stale items get a 0.5x penalty. The `current_task` parameter boosts scoring for memories whose content overlaps with your current task description.

**Output section order (never truncated):**

1. TASK (if provided)
2. BLOCKING AMBIGUITIES (high/critical impact, always shown)
3. CONFLICTS (always shown)
4. CRITICAL INVARIANTS
5. DECISIONS
6. GOTCHAS
7. REJECTED IDEAS
8. STALE WARNINGS (always shown)

Conflicts and warnings are never dropped due to token budget. Item sections truncate when budget is exceeded, with a count of omitted items noted.

## Data model

Core fields on every `MemoryItem`:

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Auto-generated primary key |
| `type` | enum | `decision`, `invariant`, `gotcha`, `rejected_idea` |
| `title` | str | Short title |
| `statement` | str | The factual claim |
| `details` | str? | Additional context |
| `tags` | list[str] | At least one required. Use `task:` or `cmd:` prefix for special types. |
| `status` | enum | `active`, `potentially_stale`, `invalidated`, `deleted` |
| `confidence` | float | 0 to 1, default 1.0 |
| `importance` | float | 0 to 1, default 0.5 |
| `evidence` | list[Evidence] | Linked source code with content hashes. Shows as `path:start-end` in context output, letting agents jump directly to the relevant code. |
| `metadata` | dict? | Type-specific keys (see memory types above) |

Evidence entries link to source code ranges with SHA256 hashes. Two purposes:
1. **Staleness detection**: if the source file changes, the memory is flagged `potentially_stale`
2. **Direct code access**: agents see `src/lib.rs:8-12` in context output and can `read` those lines without searching

## Companion skill

The [`skills/precision-first/`](skills/precision-first/SKILL.md) directory contains a precision-first software engineering methodology designed to pair with `totem`. It covers invariant management, ambiguity classification, contradiction detection, and structured code review workflows.

## Development

```bash
git clone https://github.com/emiliano-go/totem.git
cd totem
uv sync

# Run tests (none yet)
uv run pytest

# Run CLI
uv run totem --help

# Run MCP server
uv run totem-mcp
```

## License

MIT
