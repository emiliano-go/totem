---
name: totem
description: >
  Persistent memory for engineering agents. Store and retrieve decisions,
  invariants, gotchas, rejected ideas, assumptions, open questions, and
  ambiguities across sessions. Use when making architectural decisions,
  discovering non-obvious behaviors, tracking assumptions, flagging
  ambiguities, or when context would otherwise be lost between sessions.
  Triggers: decision, invariant, gotcha, assumption, ambiguity, open question,
  remember, forgot, context loss, engineering context, memory.
---

# totem Memory Skill

## Workflow phases

### 1. Discovery (session start, first encounter with codebase)

Before reading any code, load what you already know:

```
memory_recent(limit=5)
engineering_context_tool(tags=["relevant", "tags"], current_task="what you're about to do")
```

If `task:` tagged memories exist, they represent prior in-progress work. Resume from where the last session left off.

### 2. Mid-task (as you work)

Store discoveries immediately. Don't wait until the end.

**Made a decision:**
```
memory_create_tool(
  type="decision",
  title="Use FTS5 for search",
  statement="SQLite FTS5 is sufficient for our search needs",
  tags=["search", "sqlite"],
  metadata={"rationale": "No external dependency needed. Evaluated Elasticsearch (too heavy) and Whoosh (Python GIL bottleneck)."}
)
```

**Found a gotcha:**
```
memory_create_tool(
  type="gotcha",
  title="as_str_vec() panics on non-String",
  statement="Calling as_str_vec() on a non-String column causes a panic, not an error",
  tags=["rust", "types"],
  metadata={"trigger": "Passing Int or Bool column to as_str_vec()"}
)
```

**Made an assumption (§4: distinguish fact/assumption/hypothesis/guarantee):**
```
memory_create_tool(
  type="assumption",
  title="DB is PostgreSQL 14+",
  statement="The deployment target runs PostgreSQL 14 or later",
  tags=["database", "deployment"],
  metadata={
    "claimCategory": "assumption",
    "basis": "Stated in deployment docs but not verified against running instance",
    "verificationNeeded": true
  }
)
```

**Found an ambiguity (§5: classify by impact, flag blocking ones):**
```
memory_create_tool(
  type="ambiguity",
  title="Auth token expiry undefined",
  statement="Spec does not define token expiry behavior",
  tags=["auth", "api"],
  metadata={
    "question": "What is the expected behavior when an auth token expires?",
    "interpretations": [
      {"id": "1", "description": "Return 401 immediately"},
      {"id": "2", "description": "Refresh automatically and retry"},
      {"id": "3", "description": "Return 401 with refresh token in header"}
    ],
    "impact": "high"
  }
)
```

**Have an open question:**
```
memory_create_tool(
  type="open_question",
  title="Does the API support batch operations?",
  statement="Unclear if batch endpoint exists or is planned",
  tags=["api", "batch"],
  metadata={
    "question": "Does the API support batch operations for bulk imports?",
    "impact": "medium",
    "blocking": false,
    "possibleAnswers": ["Yes, POST /api/batch", "No, not yet implemented"]
  }
)
```

**Command worked or failed:**
```
memory_create_tool(
  type="gotcha",
  title="uv pip install -e . works for local dev",
  statement="Editable install from source works with uv pip, not regular pip on PEP 668 systems",
  tags=["cmd:uv-pip-install", "python"]
)
```

**Tracking in-progress work:**
```
memory_create_tool(
  type="gotcha",
  title="Auth server task: implementing JWT refresh",
  statement="Building auth server, currently on refresh token endpoint",
  tags=["task:auth-server", "auth", "jwt"]
)
```

**Existing memory is wrong?** Update it:
```
memory_update_tool(id="...", reason="Discovered edge case: FTS5 fails on unicode", statement="...")
```

### 3. Completion (task done)

Before moving on:
- Store final decisions, command outcomes, and new invariants
- If you had a `task:` tagged memory, update or delete it
- Store any commands that worked/failed with `cmd:` prefix
- Store an architecture summary of what you built (see below)

**Store an invariant for the work you did:**
```
memory_create_tool(
  type="invariant",
  title="Auth endpoints: register, login, me, refresh",
  statement="Every auth server must have these 4 endpoints",
  tags=["auth", "api", "invariant"],
  metadata={"verificationMethod": "curl test all 4 endpoints", "condition": "All return correct status codes"}
)
```

**Store an architecture summary with file references:**
```
memory_create_tool(
  type="invariant",
  title="architecture: config_store",
  statement="ConfigStore wraps Vec<(String,String)> with HashMap index. get_value() returns Option<&str>. Numeric keys use u64 (f65 lacks Hash+Eq).",
  tags=["architecture:config_store", "rust"],
  evidence=[{
    "path": "src/lib.rs",
    "startLine": 8,
    "endLine": 12,
    "contentHash": "<sha256>",
    "capturedAt": "<timestamp>"
  }],
  metadata={"verificationMethod": "cargo check", "condition": "zero warnings"}
)
```
Adding evidence lets future agents jump directly to the relevant code instead of searching.

**Store measurable outcomes (if any):**
```
memory_create_tool(
  type="gotcha",
  title="outcome: parse_batch early exit",
  statement="convert_batch() breaks on first failure. Exit code 2 = partial success. Partial output is valid; don't re-run from scratch.",
  tags=["outcome:mdtool", "performance"],
  metadata={"impact": "O(n) → O(first_failure)"}
)
```

## When to store

### Decisions
Always provide `rationale` in metadata. The default is "see statement" but a real rationale is what makes memory useful across sessions. Record:
- WHY this decision was made
- What alternatives were considered
- Why alternatives were rejected

### Invariants
Require `verificationMethod` and `condition` in metadata.

### Assumptions (§4)
Require `claimCategory` and `basis` in metadata. Classify every claim:
- `fact`: established by code, docs, tests, or runtime
- `assumption`: required to proceed, not established
- `hypothesis`: plausible explanation, unverified
- `guarantee`: necessarily follows from spec or implementation

Never present an assumption as a fact. Use calibrated language in `statement`.

### Ambiguities (§5)
Require `question`, `interpretations`, and `impact` in metadata. Impact levels:
- `low`: cosmetic, no implementation effect; proceed
- `medium`: could affect naming or minor details; pick convention
- `high`: could change API behavior, performance, or correctness; ask or state assumption
- `critical`: risk of data loss, security, or irreversible damage; never guess

Blocking ambiguities (`high|critical`) are surfaced in `engineering_context` output.

### Open Questions
Require `question`, `impact`, and `blocking` in metadata. Track what's unresolved and what would resolve it. Use `possibleAnswers` for known possibilities.

### Gotchas
No required metadata. Document non-obvious behaviors. Use `cmd:` prefix for command outcomes, `task:` prefix for in-progress work tracking.

### Rejected ideas
Require `proposal` and `reasonRejected` in metadata.

## When to retrieve

- **Start of task**: `engineering_context_tool(tags=["tag1"], task="description", current_task="what you're doing now")`
- **Quick check**: `memory_search_tool(query="relevant keywords", tags=["tag1", "tag2"])`
- **What was I doing?**: `memory_tasks_tool()`
- **Has this command been tried?**: `memory_commands_tool()`
- **Recent activity**: `memory_recent_tool(limit=5)`
- **Full context**: `engineering_context_tool` (most powerful: assembles everything relevant)

## Updating memories

Provide `reason` when updating (strongly recommended for audit trail):

```
memory_update_tool(id="...", reason="confidence lowered after discovering edge case", confidence=0.7)
```

## Conflict detection

totem automatically detects two kinds of contradictions:

1. **Evidence overlap**: Two items of the same type with overlapping file evidence but different statements
2. **Same-title contradiction**: Two items of the same type sharing a title but with different statements

Both produce structured `Conflict` objects surfaced in `engineering_context` output. Resolve with `resolve_conflict_tool`.

## Tag conventions

- `task:<name>`: In-progress work. Query with `memory_tasks_tool`.
- `cmd:<command>`: Command outcomes. Query with `memory_commands_tool`.
- `architecture:<module>`: Structural facts about a module. Use for codebase documentation.
- `outcome:<what>`: Measurable results. Use for performance wins, bug fix impact, etc.
- `edge-case:<scope>`: Relevant edge cases for a function or module.
- `verify:<scope>`: Verification results for invariants or contracts.
- `review:<scope>`: Code review findings with severity.
- Use domain tags (`auth`, `rust`, `api`) for search and context assembly.

## Hybrid memory

- **Project memories**: stored in `.totem/totem.db` (project-specific)
- **User memories**: stored in `~/.local/share/totem/totem.db` (cross-project preferences)
- `engineering_context` searches both, project first

## Initialization

For new projects, run `totem_init_tool()` first. It creates `.totem/` and the DB.

## Tips

- Use specific tags for better search results
- Link related memories with `related_memory_ids`
- Add evidence (file paths + line ranges) for staleness detection
- `current_task` on `engineering_context_tool` boosts scoring for memories relevant to what you're doing now
- Store command outcomes with `cmd:` tag so the next agent knows what works
- Classify assumptions explicitly; the difference between a fact and an assumption matters across sessions
- Flag ambiguities early: blocking ambiguities (`impact: high|critical`) are surfaced in context output
