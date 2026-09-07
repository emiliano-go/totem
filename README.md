# totem

<p align="center">
  <a href="https://pypi.org/project/totem-mcp/">
    <img src="https://img.shields.io/pypi/v/totem-mcp?logo=pypi&logoColor=white&style=for-the-badge" alt="PyPI">
  </a>
  <a href="https://pypi.org/project/totem-mcp/">
    <img src="https://img.shields.io/pypi/pyversions/totem-mcp?logo=python&logoColor=white&style=for-the-badge" alt="Python">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-10AC84?style=for-the-badge" alt="License">
  </a>
  <a href="https://modelcontextprotocol.io">
    <img src="https://img.shields.io/badge/MCP-Compatible-8A2BE2?logo=modelcontextprotocol&logoColor=white&style=for-the-badge" alt="MCP">
  </a>
  <a href="https://github.com/emiliano-go/totem/stargazers">
    <img src="https://img.shields.io/github/stars/emiliano-go/totem?logo=github&logoColor=white&style=for-the-badge" alt="Stars">
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
- **Full-text search**: Turso FTS5 on title, statement, details, and tags
- **Hybrid memory**: project memories in `.totem/`, user memories in `~/.local/share/totem/`. Context assembly searches both.
- **Context assembly**: scored pipeline with token budget support, section ordering per spec
- **Workspace scoping**: auto-detects git root for correct DB placement; explicit `--project` override available
- **Export/import**: move memories between machines or seed a new project from an existing one
- **MCP server**: expose all tools via Model Context Protocol for agent use
- **CLI**: full command-line interface for manual operations

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

## Add to your agent

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

## Quick start

### MCP server

Start the server:

```bash
totem-mcp
```

Then use it from your agent. Example tool calls:

```
memory_create_tool(type="invariant", title="No direct DB access in API layer",
  statement="API handlers must use the repository layer, never sqlite3 directly",
  tags=["api", "database"], metadata={"verificationMethod": "code review",
  "condition": "No sqlite3 imports in src/api/*.py"})

engineering_context_tool(tags=["api", "database"], task="Refactor auth middleware")
```

### CLI

```bash
# Create a memory item (rationale is optional but strongly recommended)
totem create --type decision --title "Use FTS5 for search" \
  --statement "SQLite FTS5 is sufficient for our search needs" \
  --tags "search,sqlite" --metadata '{"rationale": "No external search dependency needed"}'

# Get it back
totem get <ITEM_ID>

# Search (full-text + tags)
totem search --query "FTS5 search" --tags "sqlite"

# List recent items
totem recent

# Resolve a conflict
totem resolve <CONFLICT_ID> --resolution "Kept existing: Use FTS5"

# Export all memories to a file
totem export -o backup.json

# Import memories from a file
totem import backup.json

# Assemble context for a task
totem context --tags "search,sqlite" --task "Add fuzzy search" --budget 4096
```

## MCP tools

All tools return JSON strings. Every tool accepts an optional `project` parameter to override workspace scoping.

| Tool | Description |
|------|-------------|
| `memory_create_tool` | Create a memory item. Provide `rationale` in metadata for decisions (strongly recommended). |
| `memory_get_tool` | Retrieve by ID with staleness check |
| `memory_update_tool` | Update any field. Provide `reason` (strongly recommended for audit trail). |
| `memory_delete_tool` | Soft-delete (requires `reason`) |
| `memory_list_tool` | Filtered listing with `sort` param (`created_at`, `updated_at`, `importance`) |
| `memory_recent_tool` | List most recently created memories (default limit 5) |
| `memory_search_tool` | FTS5 full-text search (includes tags) with type/tag filters |
| `resolve_conflict_tool` | Mark a conflict as resolved with a resolution description |
| `engineering_context_tool` | Scored context assembly; searches project + user DBs |
| `memory_export_tool` | Export all memories and conflicts as portable JSON |
| `memory_import_tool` | Import memories from an export dict (skips duplicate IDs) |

## CLI commands

All commands accept `--project <path>` to override workspace scoping.

| Command | Description |
|---------|-------------|
| `totem create` | Create a new memory item |
| `totem get <ID>` | Retrieve by ID (`--no-evidence` skips staleness check) |
| `totem update <ID>` | Update an item (`--reason` optional, defaults to "maintenance") |
| `totem delete <ID>` | Soft-delete (requires `--reason`) |
| `totem list` | List with `--sort` (`created_at`, `updated_at`, `importance`) and filters |
| `totem recent` | List most recently created memories (`--limit` default 5) |
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
score = 0.4 * tag_match + 0.3 * importance + 0.2 * confidence + 0.1 * recency
```

Invariants get a 1.25x multiplier. Potentially stale items get a 0.5x penalty.

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
| `tags` | list[str] | At least one required |
| `status` | enum | `active`, `potentially_stale`, `invalidated`, `deleted` |
| `confidence` | float | 0 to 1, default 1.0 |
| `importance` | float | 0 to 1, default 0.5 |
| `evidence` | list[Evidence] | Linked source code with content hashes |
| `metadata` | dict? | Type-specific keys (see memory types above) |

Evidence entries link to source code ranges with SHA256 hashes for staleness detection.

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
