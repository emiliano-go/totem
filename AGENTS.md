# Global Agent Instructions

## totem Memory System

totem is available as an MCP server in every session. It provides persistent memory across sessions for engineering knowledge.

## Mandatory behavioral rules

These are not suggestions. Follow them on every task.

### On session start (before any code exploration)

1. Run `memory_recent(limit=5)` to see what was last worked on
2. Run `engineering_context_tool(tags=[...], current_task="...")` with tags relevant to the task at hand
3. If any `task:` tagged memories exist, read them with `memory_get_tool` to resume prior work

### During work (as you discover things)

4. **Non-obvious behavior found?** Store a gotcha immediately with `memory_create_tool(type="gotcha", ...)`. Don't defer.
5. **Made a decision?** Store it with rationale before implementing: `memory_create_tool(type="decision", ..., metadata={"rationale": "WHY..."})`
6. **About to run a command?** Check `memory_commands_tool` first for known outcomes
7. **Hit an error?** Store it as a gotcha with the `cmd:` tag prefix so the next agent doesn't repeat it
8. **Existing memory is wrong or incomplete?** Update it with `memory_update_tool(id="...", reason="...")`

### On task completion

9. Store what was learned: new gotchas, decisions, invariants discovered
10. If you were working on a `task:` tagged item, update or delete it
11. Store any command outcomes (worked/failed) with `cmd:` tag prefix

## Conventions

### Tag prefixes

- `task:<name>`: In-progress work. Use `memory_tasks_tool` to list active tasks.
- `cmd:<command>`: Command outcomes. Use `memory_commands_tool` to list known commands.

### Memory types

| Type | When to use | Required metadata |
|------|-------------|-------------------|
| `decision` | A choice that was made | `rationale` (strongly recommended) |
| `invariant` | A rule that must hold | `verificationMethod`, `condition` (required) |
| `gotcha` | Non-obvious behavior or error | (none) |
| `rejected_idea` | Proposal that was declined | `proposal`, `reasonRejected` (required) |

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

**Storage locations:**
- Project memories: `.totem/totem.db`
- User memories: `~/.local/share/totem/totem.db`

**For detailed workflows, load the `totem` skill with `skill({ name: "totem" })`.**
