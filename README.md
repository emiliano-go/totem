<p align="center">
  <img src="assets/banner.svg" alt="totem" width="100%"/>
</p>
<p align="center" style="font-size:10em;">
  <h1 align="center">totem</h1>
</p>
<p align="center">
  <em>Totem stands watch over what a tribe has learned. It does the same for your codebase.</em>
</p>
<p align="center">
  <a href="https://www.npmjs.com/package/@emiliano-go/totem">
    <img src="https://img.shields.io/npm/v/@emiliano-go/totem?logo=npm&logoColor=white&style=for-the-badge&cacheSeconds=0" alt="npm">
  </a>
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

---

A totem stands watch over what a tribe has learned. Totem does the same for a codebase. It's a Git-aware memory server for coding agents: decisions, invariants, and gotchas persist across sessions, backed by evidence, and flagged the moment the code underneath them changes.

## Why

AI coding agents lose engineering context between sessions. They re-discover the same gotchas, re-debate the same decisions, and forget invariants that were already established. `totem` persists this knowledge locally and serves it back to agents as structured context, ordered by relevance.

## Install

Requires Python 3.13+.

```bash
npm install @emiliano-go/totem
npx @emiliano-go/totem
```

The `npx` command auto-installs or upgrades the Python MCP server, pins its version, and configures enforcement plugins for OpenCode, Claude Code, and Kimi Code. For Kimi Code it also registers a user-level MCP entry (`~/.kimi-code/mcp.json`) so totem is available in every project.

### Supported agents

| Agent | Hook type | Auto-configured? |
|-------|-----------|-----------------|
| OpenCode | `tool.execute.before` JS plugin | Yes |
| Claude Code | `PreToolUse`/`PostToolUse` hooks (`~/.claude/settings.json`) | Yes |
| Kimi Code | `[[hooks]]` in `~/.kimi-code/config.toml` | Yes |

## Features

- **14 memory types** with type-specific metadata validation
- **61 MCP tools** (core, typed wrappers, and memory maintenance)
- **Staleness detection** via SHA256 content hashing on evidence
- **Conflict detection** on overlapping evidence and contradictory claims
- **Full-text search** via Turso FTS5
- **Hybrid memory** (project + user databases)
- **Context assembly** with scored pipeline and token budget
- **Agent enforcement** blocks reads/grep/bash when memory exists, forces search-first workflow
- **Commit gates** block all tools until agent registers file reads and writes
- **History audit** on every create, update, and delete
- **Tamper-evident history**: hash-chained events plus item state digests (`totem audit`)
- **Evidence-derived verification**: `memory_verify` consumes a recorded check, not a claim
- **Calibrated trust**: dependency-free Platt + split-conformal trust sets, advisory until validated
- **Hybrid ranking**: FTS + deterministic dense encoder with reciprocal-rank fusion and MMR
- **Optional static embeddings**: Model2Vec `potion-retrieval-32M` behind an extra, with a hash fallback
- **Corpus-wide retrieval**: FTS, dense, evidence paths, symbols, tags, and relation hops, not a tag gate
- **Temporal validity**: `valid_from` / `valid_to` with `as_of` retrieval
- **Feedback loop**: record useful/wrong signals and train the reranker and calibrator
- **Abstention and consolidation**: no noise when nothing is relevant; near-duplicate clustering
- **Bounded MCP tools**: output caps, rate limits, deadlines, and a tool-manifest digest
- **Memory classes**: operational, episodic, procedural, core, and semantic records
- **Entity graph**: files, symbols, components, rules, pull requests, and packages linked to memories
- **Bi-temporal edges**: recorded time plus validity windows on every relation
- **Hierarchical rollups**: file TLDRs feed module and project summaries
- **Heat and decay**: recency, importance, and access rank memories; stale operational state is archivable
- **Workflow memory**: reusable procedures with preconditions, verification, and failure modes

### Retrieval v2

`engineering_context` and `memory_search` build candidates over the whole
memory database instead of starting from tag matches. Lexical, dense, path,
symbol, tag, and two-hop relation signals are fused, then the deterministic
scope and type precedence decides the final order. Tags are a boost; a memory
without the requested tag can still surface when the task, code path, or a
relation makes it relevant.

Temporal claims:

```bash
totem create --type gotcha --title "Old queue" --statement "..." --tags queue \
  --valid-from 2026-01-01T00:00:00Z --valid-to 2026-02-01T00:00:00Z
totem context --tags queue --as-of 2026-01-15T00:00:00Z
```

Feedback and learning:

```bash
totem feedback <id> --signal useful --query "retry queue" --tags queue
totem models train --min-examples 24
totem models status
```

`memory_train_models` fits a linear reranker over candidate features and a
conformal calibrator over claim features. It refuses to train until feedback
has both positive and negative examples, so the learned path stays advisory.

Consolidation is dry-run by default:

```bash
totem consolidate --threshold 0.92
totem consolidate --threshold 0.92 --apply
```

Context returns `abstained: true` with an empty body when no memory clears the
score threshold (`TOTEM_ABSTAIN_SCORE`, default 0.12). Critical classes, path
matches, and stale knowledge still surface.

### File-state memory

File reads and writes can store one current-state TLDR per file instead of a
change log. The TLDR is deterministic and keyed by the file content hash:

```bash
totem file-context src/auth.py
```

The result includes `tldr`, `symbols`, `blobHash`, and `fresh`. An unchanged
file returns `fresh: true` without writing. A changed file returns
`changed: true` and refreshes the same memory. This is the intended cache for
avoiding re-reads: check `fresh` before asking the model to read the whole file.

`memory_session_summary` stores one compact summary per work session, keyed by
`session_id`; repeated calls update the same record.

Legacy file-change records can be converted in place:

```bash
totem files backfill
totem files backfill --apply --prune-missing
```

File-state TLDRs are excluded from generic `memory_search` results. Pass
`--include-file-state` when you want them.

### Optional static embeddings

The default dense leg is dependency-free. For better paraphrase and vocabulary
recall, install the optional static encoder:

```bash
pip install 'totem-mcp[embeddings]'
totem models pull          # downloads and warms potion-retrieval-32M
totem models status
```

`minishlab/potion-retrieval-32M` is a 32M Model2Vec model: CPU-only, numpy
based, no torch. It is downloaded from Hugging Face on first use. Configure it
with environment variables:

| Variable | Values | Meaning |
|---|---|---|
| `TOTEM_EMBEDDING_BACKEND` | `auto` (default), `model2vec`, `hash` | `auto` uses Model2Vec when installed, otherwise the hash encoder. |
| `TOTEM_EMBEDDING_MODEL` | Hugging Face id or directory | Defaults to `minishlab/potion-retrieval-32M`. |
| `TOTEM_EMBEDDING_LOCAL` | local directory | Pre-pulled model path; wins over the model id for offline installs. |

If the package or model cannot be loaded, Totem falls back to the hash encoder
and reports the reason from `totem models status`.

## How it works

_Project and user memory flow into the context compiler, which hands the agent scored, provenance-aware context._

```
             ┌─────────────────┐
             │   User facts    │
             └────────┬────────┘
                      │
             ┌────────▼────────┐
             │     Project     │
             │      memory     │
             └────────┬────────┘
                      │
       ┌──────────────┼──────────────┐
       │              │              │
   evidence       relations     provenance
       │              │              │
       └──────────────┼──────────────┘
                      │
             ┌────────▼────────┐
             │ context compiler│
             └────────┬────────┘
                      │
             ┌────────▼────────┐
             │      agent      │
             └─────────────────┘
```

```
Agent reads file for the first time
  → commit-gate blocks → agent registers read → memory stored → done

Agent reads file again (memory exists)
  → blocked → redirected to memory tools
  → searches memory → finds context → done

Agent writes a file
  → commit-gate blocks → agent registers write with reason → change documented → done

Agent reads file but finds nothing in memory
  → allowed to read → commit-gate requires registration → done
```

## Setup

### OpenCode

Add to `~/.config/opencode/opencode.json` (or let `npx @emiliano-go/totem` write it):

```json
{
  "plugin": ["@emiliano-go/totem"],
  "mcp": {
    "totem": {
      "type": "local",
      "command": ["uvx", "totem-mcp==<version>"],
      "enabled": true
    }
  }
}
```

The installer pins `<version>` to the npm package version and pre-warms the uvx cache, so agent startups use the cached environment and never hit the network.

### Claude Code

```bash
claude mcp add totem -- totem-mcp
```

### Kimi Code

MCP server and hooks are auto-configured by `npx @emiliano-go/totem`: the MCP server goes in user-level `~/.kimi-code/mcp.json` (every project), and the hooks go in `~/.kimi-code/config.toml`.

### Manual setup

If you prefer manual configuration:

```bash
# Initialize totem in your project
totem init

# For a different project
totem init --project /path/to/other
```

## Quick start

### MCP tools (from your agent)

```
# Store a decision
memory_create_tool(type="decision", title="Use FTS5 for search",
  statement="SQLite FTS5 is sufficient for our search needs",
  tags=["search", "sqlite"],
  metadata={"rationale": "No external dependency needed"})

# Get full context for a task
engineering_context_tool(tags=["api", "database"],
  current_task="Adding JWT refresh endpoint")

# Search memory
memory_search_tool(query="authentication", tags=["auth"])

# Store a command outcome
memory_create_tool(type="gotcha", title="uv pip install -e . works",
  statement="Editable install works with uv pip on PEP 668 systems",
  tags=["cmd:uv-pip-install", "python"])
```

### CLI

```bash
# Create a memory item
totem create --type decision --title "Use FTS5 for search" \
  --statement "SQLite FTS5 is sufficient for our search needs" \
  --tags "search,sqlite" --metadata '{"rationale": "No external dependency needed"}'

# Search
totem search --query "FTS5" --tags "sqlite"

# Assemble context
totem context --tags "search,sqlite" --current-task "Implementing search" --budget 4096

# Export/import
totem export -o backup.json
totem import backup.json

# Check memory quality
totem quality
```

## Memory types

| Type | Purpose | Required metadata |
|------|---------|------------------|
| `decision` | A choice that was made | `rationale` (recommended) |
| `invariant` | A rule that must hold | `verificationMethod`, `condition` |
| `gotcha` | A non-obvious pitfall | (none) |
| `rejected_idea` | A proposal declined | `proposal`, `reasonRejected` |
| `assumption` | A claim with epistemic status | `claimCategory`, `basis` |
| `open_question` | An unresolved question | `question`, `impact`, `blocking` |
| `ambiguity` | An ambiguous requirement | `question`, `interpretations`, `impact` |
| `contract` | Observable behavior | `subject` |
| `constraint` | Implementation restriction | `constraint` |
| `hypothesis` | Plausible explanation | `hypothesis` |
| `observation` | Something seen in code | `observation` |
| `bug` | A defect with state machine | `symptom`, `severity`, `state` |
| `architecture` | Component mapping | `component`, `responsibility` |
| `implementation` | Codebase facts | `subject`, `kind`, `path` |

## MCP tools (61)

| Tool | Description |
|------|-------------|
| `totem_init_tool` | Initialize totem for a project |
| `memory_create_tool` | Create a memory item (warns on duplicate title) |
| `memory_get_tool` | Retrieve by ID with staleness check |
| `memory_update_tool` | Update any field (provides audit trail) |
| `memory_delete_tool` | Soft-delete (requires `reason`) |
| `memory_list_tool` | Filtered listing with sort and type/tag filters |
| `memory_recent_tool` | List recently created memories |
| `memory_tasks_tool` | List in-progress task memories (`task:*`) |
| `memory_commands_tool` | List command outcomes (`cmd:*`) |
| `memory_search_tool` | FTS5 full-text search with type/tag filters |
| `resolve_conflict_tool` | Mark conflict as resolved |
| `engineering_context_tool` | Scored context assembly with task relevance |
| `memory_export_tool` | Export all memories as JSON |
| `memory_import_tool` | Import memories from JSON (skips duplicates) |
| `memory_gc_tool` | Purge terminal-state memories past retention (dry-run default) |
| `memory_quality_tool` | Report signal ratio, provenance, stale evidence, conflicts, and digest composition |
| `memory_workflow_tool` | Create or update a reusable procedure |
| `memory_profile_tool` | Write one always-on user profile block |
| `memory_graph_tool` | Show entities and the memories linked to them |
| `memory_rollup_tool` | Summarize file state into module and project memories |
| `memory_heat_tool` | Rank memories by recency, importance, and access |
| `memory_touch_tool` | Record that memories were read, feeding heat |
| `memory_decay_tool` | Archive low-heat operational and episodic memories |
| `memory_induce_workflow_tool` | Turn a session summary into a workflow draft |
| `memory_maintenance_tool` | Run revalidation, rollup, decay, and consolidation |
| `memory_export_markdown_tool` | Export memories as Markdown notes |
| `memory_import_markdown_tool` | Import Markdown notes with wikilink relations |
| `memory_verify_tool` | Record that a memory was verified (sets verified_at/commit, confidence 1.0) |
| `memory_revalidate_tool` | Void verifications whose evidence changed |
| `register_file_read_tool` | Store facts learned from reading a file (auto-hashes) |
| `register_file_write_tool` | Register file changes with reason (auto-hashes) |
| `*_create` (14) | Typed wrappers for each memory type |
| `flag_ambiguity` | Convenience wrapper for ambiguity creation |

## CLI commands (39)

| Command | Description |
|---------|-------------|
| `totem init` | Initialize totem and install agent instructions |
| `totem create` | Create a new memory item |
| `totem get <ID>` | Retrieve by ID |
| `totem update <ID>` | Update an item |
| `totem delete <ID>` | Soft-delete (requires `--reason`) |
| `totem list` | List with filters |
| `totem recent` | List recently created memories |
| `totem tasks` | List in-progress task memories |
| `totem commands` | List command outcomes |
| `totem resolve <ID>` | Mark conflict as resolved |
| `totem search` | Full-text search |
| `totem export` | Export memories as JSON |
| `totem import <FILE>` | Import memories from JSON |
| `totem gc` | Purge terminal-state memories past retention (`--apply`) |
| `totem verify <ID>` | Record that a memory was verified |
| `totem revalidate` | Void verifications whose evidence changed (`--apply`) |
| `totem context` | Assemble scored context |

## Context assembly

**Scoring formula:**
```
score = 0.30*tag_match + 0.20*task_similarity + 0.25*importance
      + 0.15*confidence + 0.10*recency
```

Invariants, constraints, and ambiguities get a 1.25x multiplier. Potentially stale items get a 0.5x penalty.

**Output sections (BLOCKING AMBIGUITIES, CONFLICTS, and STALE WARNINGS are never budget-truncated):**

1. TASK (if provided)
2. BLOCKING AMBIGUITIES
3. CONTEXT CONFLICTS
4. CRITICAL CONSTRAINTS
5. CRITICAL INVARIANTS
6. RELEVANT CONTRACTS
7. ARCHITECTURE
8. DECISIONS
9. KNOWN AMBIGUITIES (non-blocking)
10. OBSERVATIONS
11. GOTCHAS
12. KNOWN BUGS
13. HYPOTHESES
14. CODEBASE FACTS
15. OPEN QUESTIONS
16. REJECTED IDEAS
17. STALE KNOWLEDGE WARNINGS

## Workspace scoping

totem auto-detects your project root via `git rev-parse --show-toplevel`. Override with `--project <path>` on any CLI command or `project` parameter on any MCP tool.

## Hybrid memory

- **Project memories**: `.totem/totem.db`
- **User memories**: `~/.local/share/totem/totem.db`

`engineering_context` searches both, with project memories taking precedence.

## Trust and provenance

`asserted_by` records **who asserted a claim** (user, test, source, git, doc,
runtime, agent); it is not a verification signal. Each provenance sets a default
confidence: test `0.95`, user/source/git/doc `0.9`, runtime `0.7`, agent `0.6`
(hypotheses cap at `0.4`). Nothing reaches `1.0` by provenance alone. A
user-asserted claim is trusted, not proven.

Verification is separate and explicit. `memory_verify` (`totem verify <ID>`)
records that a claim was checked: it sets `verified_at` and `verified_commit`
(HEAD by default), raises confidence to `1.0`, and writes a history event; an
optional `verified_by` relation links the verification artifact. A verification
holds only while its **evidence still matches** (truth maintenance: evidence is
the premise, the verification the derived belief). When evidence changes, the
verification is retracted and confidence drops back to its provenance default;
`memory_get` does this on read, and `memory_revalidate` (`totem revalidate`)
scans for it. The enforcement hook clears its `verify:<file>` gate only when a
fresh verification record exists.

## Deployment boundary

Totem is a **single-user, local memory layer**. It has no tenancy or
authorization model, and this is intentional:

- the user DB lives at `~/.local/share/totem/totem.db`, owned by the OS user
  that runs totem (override with `TOTEM_USER_DB`);
- the project DB lives in the project's `.totem/` directory, scoped to whoever
  can read the checkout;
- MCP tools run with the full privileges of that user.

Do not expose a totem MCP server to multiple untrusted users on one host, and do
not point several OS users at one shared user DB. If you need multi-user
isolation, run one totem instance (and one user DB) per user. The boundary is
enforced by the filesystem and the OS user, not by totem itself.

## Tag conventions

- `task:<name>`: In-progress work. Query with `memory_tasks_tool`.
- `cmd:<command>`: Command outcomes. Query with `memory_commands_tool`.
- `architecture:<module>`: Structural facts about a module.
- `outcome:<what>`: Measurable results (performance wins, bug fix impact, etc.).

## Companion skill

The [`skills/precision-first/`](skills/precision-first/SKILL.md) directory contains a precision-first software engineering methodology designed to pair with `totem`.

## Development

```bash
# Run JS tests
cd plugins/totem-enforce && node test-tokenize.js

# Run Python package tests
uv run pytest

# Run Python hook tests
cd plugins/totem-enforce && python3 test-enforce.py
```

## License

MIT
