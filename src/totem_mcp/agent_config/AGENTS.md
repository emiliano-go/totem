# Global Agent Instructions

## totem Memory System

totem is available as an MCP server in every session. It provides persistent memory across sessions for engineering knowledge.

## Two types of information

totem tracks two categories of knowledge:

### Engineering info (WHY / HOW)
Decisions, invariants, gotchas, assumptions, ambiguities, contracts,
constraints, hypotheses, observations, bugs, architecture. These explain
why the code is the way it is.

### Implementation info (WHAT)
Codebase facts: API interfaces, function signatures, module structure,
file summaries, type definitions. These describe what the code does.

**Store implementation info after reading files.** This prevents re-reading
the same code in future sessions.

## Mandatory behavioral rules

These are not suggestions. Follow them on every task.

### On session start (before any code exploration)

1. Run `memory_recent(limit=5)` to see what was last worked on
2. Run `engineering_context_tool(tags=[...], current_task="...")` with tags relevant to the task at hand
3. If any `task:` tagged memories exist, read them with `memory_get_tool` to resume prior work

### During work: totem first, then codebase

**Before searching or reading code:**

4. **Before grep/glob?** Search memory first: `memory_search_tool(query="...", tags=["..."])`. Only grep if memory returns nothing relevant.
5. **Before reading a file?** Check context first: `engineering_context_tool(tags=["..."], paths=["src/file.py"])`. Only read if memory has no relevant context.
6. **Before running a command?** Check known outcomes: `memory_commands_tool()`. Only run if no matching outcome exists.

**As you discover things:**

7. **Non-obvious behavior found?** Store a gotcha immediately with `memory_create_tool(type="gotcha", ...)`. Don't defer.
8. **Made an architectural/implementation decision?** (not task progress) Store it with rationale before implementing: `memory_create_tool(type="decision", ..., metadata={"rationale": "WHY..."})`
9. **Found an assumption?** Store it with claimCategory and basis: `memory_create_tool(type="assumption", ..., metadata={"claimCategory": "assumption", "basis": "WHY..."})`
10. **Found an ambiguity?** Flag it with impact level: `memory_create_tool(type="ambiguity", ..., metadata={"question": "...", "interpretations": [...], "impact": "high"})`
11. **Have an open question?** Track it: `memory_create_tool(type="open_question", ..., metadata={"question": "...", "impact": "medium", "blocking": false})`
12. **Hit an error?** Store it as a gotcha with the `cmd:` tag prefix so the next agent doesn't repeat it
13. **Existing memory is wrong or incomplete?** Update it with `memory_update_tool(id="...", reason="...")`

### After reading code: store what you learned

14. **After reading a file?** Store key facts as implementation memory:
    ```
    memory_create_tool(type="implementation", title="File: auth.py",
      statement="getUser() returns User | null, takes user_id: int",
      tags=["implementation", "auth", "api"],
      metadata={"subject": "getUser()", "kind": "api", "path": "src/auth.py"})
    ```
15. **After understanding a module?** Store architecture:
    ```
    memory_create_tool(type="architecture", title="Auth module",
      statement="Handles JWT tokens and session management",
      tags=["architecture:auth"],
      metadata={"component": "auth", "responsibility": "JWT + sessions"})
    ```

### On task completion

16. Store what was learned: new gotchas, decisions, invariants discovered
17. If you were working on a `task:` tagged item, update or delete it
18. Store any command outcomes (worked/failed) with `cmd:` tag prefix
19. **After completing a task**, store an architecture summary: what you built, key structural decisions, and any measurable outcomes. Use `architecture:<module>` or `outcome:<what>` tags.

## Anti-patterns

### Do not store task progress as decisions
❌ `type="decision", title="Phase A complete"`: this is progress, not a decision
✅ `type="decision", title="13 types over 4", metadata={"rationale": "..."}`: this explains WHY

### Do not store without the "future agent" test
Before every `memory_create`, ask: "Would this help another agent in a future session?"
If no, don't store it.

### Do not store trivial operations
❌ `type="gotcha", title="Ran pytest, tests passed"`
✅ `type="gotcha", title="FTS5 drops unicode chars"`: non-obvious behavior

### Do not re-read files that are already in memory
If `implementation_create` exists for a file, use the stored summary instead of reading the file again.

## Conventions

### Tag prefixes

- `task:<name>`: In-progress work. Use `memory_tasks_tool` to list active tasks.
- `cmd:<command>`: Command outcomes. Use `memory_commands_tool` to list known commands.
- `architecture:<module>`: Structural facts about a module. Use for codebase documentation.
- `outcome:<what>`: Measurable results. Use for performance wins, bug fix impact, etc.
- `edge-case:<scope>`: Relevant edge cases for a function or module.
- `verify:<scope>`: Verification results (pass/fail) for invariants or contracts.
- `review:<scope>`: Code review findings with severity (critical/important/minor/style).

### Memory types

| Type | When to use | Required metadata |
|------|-------------|-------------------|
| `decision` | A choice that was made | `rationale` (strongly recommended) |
| `invariant` | A rule that must hold | `verificationMethod`, `condition` (required) |
| `gotcha` | Non-obvious behavior or error | (none) |
| `rejected_idea` | Proposal that was declined | `proposal`, `reasonRejected` (required) |
| `assumption` | A claim with a specific epistemic status | `claimCategory` (fact/assumption/hypothesis/guarantee), `basis` (required) |
| `open_question` | An unresolved question blocking or informing work | `question`, `impact` (low/medium/high/critical), `blocking` (required) |
| `ambiguity` | An ambiguous requirement or specification | `question`, `interpretations` (list of {id, description}), `impact` (required) |
| `implementation` | A codebase fact (API, function, module, type) | `subject`, `kind` (api/function/module/type/config/schema), `path` (required) |

### Implementation info kinds

| Kind | What to store | Example |
|------|---------------|---------|
| `api` | Function/method signatures, return types | "getUser(user_id: int) → User \| null" |
| `function` | Function behavior, side effects | "hash_content() returns SHA256 hex string" |
| `module` | File structure, exports, responsibilities | "db.py: Turso storage layer, all CRUD operations" |
| `type` | Type definitions, struct layouts | "MemoryItem: 15 fields, 4 required" |
| `config` | Configuration schema, env vars | "SCHEMA_VERSION = 3 in db.py" |
| `schema` | Database schemas, migration state | "memory_items table: 17 columns" |

### Claim categories (§4 of precision-first methodology)

When using `assumption` type, classify the claim:

| Category | Meaning | Example |
|----------|---------|---------|
| `fact` | Established by code, docs, tests, or runtime | "The API returns 200 on success" |
| `assumption` | Required to proceed, not established | "The DB is PostgreSQL 14+" |
| `hypothesis` | Plausible explanation, unverified | "The race condition is in the cache layer" |
| `guarantee` | Necessarily follows from spec or implementation | "IDs are unique (primary key constraint)" |

### Ambiguity impact levels (§5 of precision-first methodology)

| Level | Meaning | Action |
|-------|---------|--------|
| `low` | Cosmetic, no implementation effect | Proceed |
| `medium` | Could affect naming or minor details | Pick convention, state if useful |
| `high` | Could change API behavior, performance, or correctness | Ask or implement under explicit assumption |
| `critical` | Risk of data loss, security, or irreversible damage | Never guess. Ask or tightly constrain. |

Blocking ambiguities (`impact: high|critical`) are surfaced in `engineering_context` output and must be resolved before proceeding.

## Enforcement hooks

totem enforces its workflow via agent hooks (see `plugins/totem-enforce/`):

- Before grep/glob: search memory first
- Before read: check engineering_context first
- Before bash: check memory_commands first
- After read: store implementation info automatically

If memory is empty (first session), hooks allow everything.
If memory has relevant items, hooks block and redirect to totem tools.

## Tools available

- `totem_init_tool` - Initialize totem for a project
- `memory_create_tool` - Store new memories
- `memory_get_tool` - Retrieve by ID with staleness check
- `memory_search_tool` - Full-text search (includes tags)
- `memory_list_tool` - Filtered listing with sort
- `memory_recent_tool` - List most recently created memories
- `memory_tasks_tool` - List in-progress task memories (tagged task:*)
- `memory_commands_tool` - List command outcomes (gotchas tagged cmd:*)
- `memory_update_tool` - Update existing memories
- `memory_delete_tool` - Soft-delete
- `resolve_conflict_tool` - Mark a conflict as resolved
- `engineering_context_tool` - Assemble full context for a task
- `memory_export_tool` - Export all memories as JSON
- `memory_import_tool` - Import memories from export

**Typed wrappers** (call `memory_create_tool` with pre-filled type):

- `decision_create`, `invariant_create`, `gotcha_create`, `rejected_idea_create`
- `assumption_create`, `open_question_create`, `ambiguity_create`
- `contract_create`, `constraint_create`
- `hypothesis_create`, `observation_create`
- `bug_create`, `architecture_create`
- `implementation_create`
- `flag_ambiguity` (convenience for ambiguity_create)

**Storage locations:**
- Project memories: `.totem/totem.db`
- User memories: `~/.local/share/totem/totem.db`

**For detailed workflows, load the `totem` skill with `skill({ name: "totem" })`.**
