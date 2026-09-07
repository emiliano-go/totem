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

The plugin intercepts `read`, `grep`, `glob`, and `bash` tool calls (including bash sub-commands like `grep`, `find`, `cat`, `sed`, etc.). When totem has memory related to what you're accessing, it blocks the call and redirects you to search memory first.

```
# Without totem: agent reads file directly
read /path/to/file.py

# With totem: agent is forced to check memory first
read /path/to/file.py
  → "Totem has memory about this. Use engineering_context_tool first."
  → agent searches memory, finds relevant context
  → agent reads file with full context
```

## Supported agents

| Agent | Hook type | Auto-configured? |
|-------|-----------|-----------------|
| OpenCode | `tool.execute.before` JS plugin | Yes (`npx totem`) |
| Claude Code | `PreToolUse` hooks (`.claude/settings.json`) | Yes (`npx totem`) |
| Kimi Code | `PreToolUse` hooks (`~/.kimi-code/config.toml`) | Yes (`npx totem`) |

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
