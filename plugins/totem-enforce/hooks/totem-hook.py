#!/usr/bin/env python3
"""Totem enforcement hook for Claude Code and Kimi Code.

Single script, three subcommands:

  pre    PreToolUse (matcher ".*") — read gate + commit-gate enforcement
  post   PostToolUse (matcher "Read|Edit|Write|MultiEdit|NotebookEdit") — arm gates
  clear  UserPromptSubmit — reset per-turn state

Behavior:
  1. Read gate: if an implementation memory exists for the file path, deny the
     Read and redirect to memory. The retry (after the agent checks memory) is
     allowed via the per-turn "searched" cache.
  2. Read commit-gate: after a successful Read, all non-totem tools are denied
     until mcp__totem__register_file_read_tool is called.
  3. Write commit-gate: after Edit/Write/MultiEdit/NotebookEdit, all non-totem
     tools are denied until mcp__totem__register_file_write_tool is called.

Always fails open: any error, missing CLI, or timeout results in allow.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# Sub-commands that search/read file content
SUBCMDS = re.compile(
    r"^(grep|find|cat|head|tail|wc|sort|uniq|awk|sed|less|more|diff|comm|xargs|file|rg|ag|ack|jq)$"
)
STOP_WORDS = {"the", "and", "for", "not", "with", "from", "this", "that"}
FTS5_SPECIAL = re.compile(r'[:"\'+*^()~]')

MCP_PREFIX = "mcp__totem__"
REGISTER_READ = "mcp__totem__register_file_read_tool"
REGISTER_WRITE = "mcp__totem__register_file_write_tool"

READ_TOOLS = ("Read", "read")
WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit", "edit", "write")
SEARCH_TOOLS = ("Grep", "grep", "Glob", "glob", "Bash", "bash")


# ── State ─────────────────────────────────────────────────────────


def get_state_path(session_id: str) -> Path:
    # Sanitize: session_id comes from the hook payload; keep the filename safe.
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)
    return Path(tempfile.gettempdir()) / f"totem-hook-state-{safe}.json"


def load_state(session_id: str) -> dict:
    path = get_state_path(session_id)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"searched": {}, "pending_read": None, "pending_write": None}


def save_state(session_id: str, state: dict) -> None:
    try:
        get_state_path(session_id).write_text(json.dumps(state))
    except OSError:
        pass


# ── Memory check ──────────────────────────────────────────────────


def totem_search(query: str, project_dir: str, *, types: str | None = None,
                 tags: str | None = None) -> bool:
    """Return True if totem has at least one memory matching the query."""
    try:
        # --project is a group-level option: it must precede the subcommand.
        cmd = ["totem", "--project", project_dir, "search",
               "--query", query, "--limit", "1"]
        if types:
            cmd.extend(["--types", types])
        if tags:
            cmd.extend(["--tags", tags])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return False
        items = json.loads(result.stdout) if result.stdout.strip() else []
        return len(items) > 0
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return False  # Fail open


def tokenize(text: str) -> list[str]:
    sanitized = FTS5_SPECIAL.sub(" ", text)
    words = re.split(r"[/\\._\- ='\"{},]+", sanitized)
    words = [w.lower() for w in words if len(w) > 2 and w.lower() not in STOP_WORDS]
    return list(dict.fromkeys(words))


def tokenize_bash(command: str) -> list[str]:
    cmd = command.strip()
    cmd = re.sub(r"^cd\s+\S+\s*&&\s*", "", cmd)
    cmd = re.sub(r"^cd\s+\S+\s*;\s*", "", cmd)
    parts = cmd.split()
    if not parts:
        return []
    first = parts[0].split("/")[-1].lower()
    if SUBCMDS.match(first):
        return tokenize(cmd)
    return [first] if len(first) > 2 else []


def build_search_key(tool_name: str, tool_input: dict) -> str:
    if tool_name in ("Grep", "grep"):
        return f"grep:{tool_input.get('pattern', tool_input.get('regex', ''))}"
    if tool_name in ("Glob", "glob"):
        return f"glob:{tool_input.get('pattern', '')}"
    if tool_name in READ_TOOLS:
        return f"read:{tool_input.get('filePath', tool_input.get('file_path', ''))}"
    if tool_name in ("Bash", "bash"):
        return f"bash:{tool_input.get('command', '')}"
    return ""


def has_memory_for(tool_name: str, tool_input: dict, project_dir: str) -> bool:
    """Dispatch the memory check per tool kind."""
    if tool_name in READ_TOOLS:
        file_path = tool_input.get("filePath", tool_input.get("file_path", ""))
        if not file_path:
            return False
        # Gate on real file memories only (implementation kind, matching path).
        if totem_search(file_path, project_dir, types="implementation"):
            return True
        # Also gate on the file name alone (path separators hurt FTS).
        name = Path(file_path).name
        return bool(name) and totem_search(name, project_dir, types="implementation")
    if tool_name in ("Grep", "grep", "Glob", "glob"):
        pattern = tool_input.get("pattern", tool_input.get("regex", ""))
        for word in tokenize(pattern):
            if totem_search(word, project_dir):
                return True
        return False
    if tool_name in ("Bash", "bash"):
        command = tool_input.get("command", "")
        first = command.strip().split()[0].split("/")[-1].lower() if command.strip() else ""
        if not SUBCMDS.match(first):
            # Plain command: only gate on known cmd: outcomes.
            return bool(first) and totem_search(first, project_dir, tags=f"cmd:{first}")
        for word in tokenize_bash(command):
            if totem_search(word, project_dir):
                return True
        return False
    return False


# ── Decisions ─────────────────────────────────────────────────────


def deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def allow() -> None:
    sys.exit(0)


# ── Subcommands ───────────────────────────────────────────────────


def cmd_pre(payload: dict) -> None:
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    project_dir = payload.get("cwd", os.getcwd())
    session_id = payload.get("session_id") or str(os.getppid())

    state = load_state(session_id)

    # 1. Register calls clear their gate.
    if tool_name == REGISTER_READ:
        state["pending_read"] = None
        save_state(session_id, state)
        allow()
    if tool_name == REGISTER_WRITE:
        state["pending_write"] = None
        save_state(session_id, state)
        allow()

    # 2. Commit-gate: pending registration blocks all non-totem tools.
    if not tool_name.startswith(MCP_PREFIX):
        if state.get("pending_read"):
            deny(
                f"You read {state['pending_read']}. You MUST call "
                f"register_file_read_tool with what you learned before doing "
                f"anything else (subject, kind, statement, tags)."
            )
        if state.get("pending_write"):
            deny(
                f"You modified {state['pending_write']}. You MUST call "
                f"register_file_write_tool documenting what changed and why "
                f"before doing anything else."
            )

    # 3. Memory gates for search/read tools.
    if tool_name in READ_TOOLS or tool_name in SEARCH_TOOLS:
        search_key = build_search_key(tool_name, tool_input)
        if not search_key:
            allow()
        if search_key in state.get("searched", {}):
            allow()  # Already blocked once this turn; agent checked memory.
        if has_memory_for(tool_name, tool_input, project_dir):
            state.setdefault("searched", {})[search_key] = True
            save_state(session_id, state)
            if tool_name in READ_TOOLS:
                redirect = "engineering_context_tool (with paths=[...]) or memory_search_tool"
            elif tool_name in ("Bash", "bash"):
                redirect = "memory_commands_tool"
            else:
                redirect = "memory_search_tool"
            deny(
                f"Totem has memory about this. Use {redirect} first. "
                f"Only {tool_name} the codebase if memory returns nothing relevant. "
                f"Do not bypass via another tool."
            )

    allow()


def cmd_post(payload: dict) -> None:
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    session_id = payload.get("session_id") or str(os.getppid())

    file_path = tool_input.get("filePath", tool_input.get("file_path", ""))
    if not file_path:
        allow()

    state = load_state(session_id)
    if tool_name in READ_TOOLS:
        state["pending_read"] = file_path
    elif tool_name in WRITE_TOOLS:
        state["pending_write"] = file_path
    else:
        allow()
    save_state(session_id, state)
    allow()


def cmd_clear(payload: dict) -> None:
    session_id = payload.get("session_id") or str(os.getppid())
    path = get_state_path(session_id)
    if path.exists():
        save_state(session_id, {"searched": {}, "pending_read": None, "pending_write": None})
    sys.exit(0)


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("pre", "post", "clear"):
        sys.exit(0)  # Unknown usage → fail open

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        sys.exit(0)

    {"pre": cmd_pre, "post": cmd_post, "clear": cmd_clear}[sys.argv[1]](payload)


if __name__ == "__main__":
    main()
