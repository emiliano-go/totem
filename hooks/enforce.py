#!/usr/bin/env python3
"""Totem enforcement hook. Reads JSON from stdin, checks memory, decides allow/block.

Used by: OpenCode, Claude Code, Kimi Code.
Smart blocking: only block if memory is non-empty AND related to the query.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def get_state_path() -> Path:
    """Get path to state file for this session."""
    session_id = os.environ.get("SESSION_ID", os.getppid())
    return Path(tempfile.gettempdir()) / f"totem-hook-state-{session_id}.json"


def load_state() -> dict:
    """Load hook state from temp file."""
    path = get_state_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"searched_this_turn": {}}


def save_state(state: dict) -> None:
    """Save hook state to temp file."""
    path = get_state_path()
    try:
        path.write_text(json.dumps(state))
    except OSError:
        pass


def check_memory(query: str, project_dir: str) -> bool:
    """Check if totem has memory related to this query."""
    try:
        result = subprocess.run(
            ["totem", "search", "--query", query, "--limit", "1", "--project", project_dir],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False
        items = json.loads(result.stdout) if result.stdout.strip() else []
        return len(items) > 0
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return False  # Fail open


def build_search_key(tool_name: str, tool_input: dict) -> str:
    """Build a search key from tool name and input."""
    if tool_name in ("Grep", "grep"):
        return f"grep:{tool_input.get('pattern', tool_input.get('regex', ''))}"
    if tool_name in ("Glob", "glob"):
        return f"glob:{tool_input.get('pattern', '')}"
    if tool_name in ("Read", "read"):
        return f"read:{tool_input.get('filePath', '')}"
    if tool_name in ("Bash", "bash"):
        return f"bash:{tool_input.get('command', '')}"
    return ""


def main() -> None:
    """Main hook entry point."""
    # Read event data from stdin
    input_data = sys.stdin.read()
    try:
        payload = json.loads(input_data)
    except json.JSONDecodeError:
        sys.exit(0)  # Fail open

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    project_dir = payload.get("cwd", os.getcwd())

    # Only intercept search/read tools
    if tool_name not in ("Grep", "grep", "Glob", "glob", "Read", "read", "Bash", "bash"):
        sys.exit(0)  # Allow non-intercepted tools

    # Build search key
    search_key = build_search_key(tool_name, tool_input)
    if not search_key:
        sys.exit(0)  # Allow if can't build key

    # Load state
    state = load_state()

    # Check if already searched this turn
    if search_key in state.get("searched_this_turn", {}):
        sys.exit(0)  # Allow - already searched

    # Check memory
    has_memory = check_memory(search_key, project_dir)
    if not has_memory:
        sys.exit(0)  # No memory → allow

    # Memory exists → block
    state.setdefault("searched_this_turn", {})[search_key] = "now"
    save_state(state)

    # Determine tool category for redirect message
    if tool_name in ("Grep", "grep", "Glob", "glob"):
        redirect = "memory_search_tool"
    elif tool_name in ("Read", "read"):
        redirect = "engineering_context_tool"
    elif tool_name in ("Bash", "bash"):
        redirect = "memory_commands_tool"
    else:
        redirect = "memory_search_tool"

    reason = (
        f"Totem has memory about this. Use {redirect} first. "
        f"Only {tool_name} the codebase if memory returns nothing relevant."
    )

    # Output JSON decision (works for Claude Code and Kimi Code)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


if __name__ == "__main__":
    main()
