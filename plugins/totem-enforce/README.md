# totem

Agent enforcement plugin for the [totem](https://github.com/emiliano-go/totem) memory system. Forces agents to search totem memory before reading, grepping, or running bash commands.

## Install

```bash
npm install totem
```

This installs:
- Enforcement plugins for OpenCode, Claude Code, and Kimi Code (auto-configured by `npx totem`)
- The `totem-mcp` Python MCP server (auto-installed via uvx or pip)

## How it works

The plugin intercepts `read`, `grep`, `glob`, and `bash` tool calls. When totem has memory related to what you're accessing, it blocks the call and redirects you to search memory first.

```
# Without totem: agent reads file directly
read /path/to/file.py

# With totem: agent is forced to check memory first
read /path/to/file.py
  → "Totem has memory about this. Use engineering_context_tool first."
  → agent searches memory, finds relevant context
  → agent reads file with full context
```

## Setup

### opencode

Add to `~/.config/opencode/opencode.json`:

```json
{
  "plugin": ["totem"],
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
claude mcp add totem -- uvx totem-mcp
```

### Claude Desktop / Cursor / Windsurf

Add to config:

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

## Requirements

- Python 3.13+ (for the MCP server)
- `uvx` or `pip` (auto-installed by the plugin)

## License

MIT
