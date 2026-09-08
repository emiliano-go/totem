# totem

Agent enforcement plugin for the [totem](https://github.com/emiliano-go/totem) memory system. Forces agents to search totem memory before reading, grepping, or running bash commands. Supports OpenCode, Claude Code, and Kimi Code.

## Install

```bash
npm install @emiliano-go/totem
```

This installs:
- Enforcement plugins for OpenCode, Claude Code, and Kimi Code (auto-configured by `npx totem`)
- The `totem-mcp` Python MCP server (auto-installed via uvx or pip)

## How it works

The plugin enforces a memory-first workflow with three gates:

1. **Memory gate** — intercepts `read`, `grep`, `glob`, and `bash` tool calls (including bash sub-commands like `grep`, `find`, `cat`, `sed`, etc.). When totem has memory related to what you're accessing, the call is blocked and the agent is redirected to search memory first. The retry after checking memory is allowed.
2. **Read commit-gate** — after a successful file read, all non-totem tools are blocked until the agent calls `register_file_read_tool` with what it learned.
3. **Write commit-gate** — after `edit`/`write`, all non-totem tools are blocked until the agent calls `register_file_write_tool` documenting the change.

```
# Without totem: agent reads file directly
read /path/to/file.py

# With totem: agent is forced to check memory first
read /path/to/file.py
  → "Totem has memory about this. Use engineering_context_tool first."
  → agent searches memory, finds relevant context
  → agent reads file with full context
  → agent must call register_file_read_tool before doing anything else
```

No stub memories are auto-created — the agent itself is responsible for
recording what it learned, which keeps memory quality high.

## Supported agents

| Agent | Hook type | Auto-configured? |
|-------|-----------|-----------------|
| OpenCode | `tool.execute.before` JS plugin | Yes (`npx totem`) |
| Claude Code | `PreToolUse`/`PostToolUse` hooks (`~/.claude/settings.json`) | Yes (`npx totem`) |
| Kimi Code | plugin hooks (`kimi.plugin.json`) | Yes (`npx totem`) |

Known limitations:

- OpenCode does not pass MCP tool arguments to `tool.execute.before`, so the commit-gates clear on any `totem_register_file_read/write_tool` call regardless of its path argument.
- OpenCode `tool.execute.before` does not fire inside task-spawned subagents.
- Kimi Code plugin hooks do not fire in `kimi -p` print mode (use config.toml `[[hooks]]` there — see `kimi-hooks.toml`).

## Setup

### OpenCode

```bash
npx @emiliano-go/totem
```

Or manually add to `~/.config/opencode/opencode.json`:

```json
{
  "plugin": ["@emiliano-go/totem"],
  "mcp": {
    "totem": {
      "type": "local",
      "command": ["uvx", "totem-mcp"],
      "enabled": true
    }
  }
}
```

### Claude Code

```bash
npx @emiliano-go/totem
# Or manually:
claude mcp add totem -- uvx totem-mcp
```

### Kimi Code

```bash
npx @emiliano-go/totem
```

## Requirements

- Python 3.13+ (for the MCP server)
- `uvx` or `pip` (auto-installed by the plugin)

## Development

```bash
# Run JS tests
node test-tokenize.js

# Run Python tests
python3 test-enforce.py
```

## License

MIT
