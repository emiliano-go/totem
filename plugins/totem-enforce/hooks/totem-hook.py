#!/usr/bin/env python3
"""Totem enforcement hook for Claude Code and Kimi Code.

Single script, three subcommands:

  pre    PreToolUse (matcher ".*"): read gate + commit-gate enforcement
  post   PostToolUse (matcher "Read|Edit|Write|MultiEdit|NotebookEdit"): arm gates
  clear  UserPromptSubmit: reset per-turn state

Behavior:
  1. Read gate: if an implementation memory exists for the file path, deny the
     Read and redirect to memory. The retry (after the agent checks memory) is
     allowed via the per-turn "searched" cache.
  2. Read commit-gate: after a successful Read, all non-totem tools are denied
     until mcp__totem__register_file_read_tool is called for that file. Gates
     are per file, so parallel reads each need their own registration.
  3. Write commit-gate: after Edit/Write/MultiEdit/NotebookEdit, all non-totem
     tools are denied until mcp__totem__register_file_write_tool is called.
  4. Verify gate: reading a file that has an invariant/constraint tagged
     verify:<file> requires the registration to carry a verify tag.

Always fails open: any error, missing CLI, or timeout results in allow.
"""

import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

# Enforcement policy: off | warn | normal | strict.
#   off    - never block
#   warn   - never block, print the would-be denial to stderr
#   normal - block on gates, fail open on errors (default)
#   strict - like normal, but fail closed when a check cannot complete
ENFORCEMENT = os.environ.get("TOTEM_ENFORCEMENT", "normal").strip().lower()

# Sub-commands that search/read file content
SUBCMDS = re.compile(
    r"^(grep|find|cat|head|tail|wc|sort|uniq|awk|sed|less|more|diff|comm|xargs|file|rg|ag|ack|jq)$"
)
STOP_WORDS = {"the", "and", "for", "not", "with", "from", "this", "that"}
FTS5_SPECIAL = re.compile(r'[:"\'+*^()~]')

# Max terms per batched FTS5 query. Bounds every memory gate to ONE totem
# subprocess per tool call no matter how many words the pattern has.
MAX_SEARCH_TERMS = 5

MCP_PREFIX = "mcp__totem__"
REGISTER_READ = "mcp__totem__register_file_read_tool"
REGISTER_WRITE = "mcp__totem__register_file_write_tool"

READ_TOOLS = ("Read", "read")
WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit", "edit", "write")
SEARCH_TOOLS = ("Grep", "grep", "Glob", "glob", "Bash", "bash")


def input_path(tool_input: dict) -> str:
    # Claude Code sends file_path/filePath; Kimi Code sends path.
    return tool_input.get("filePath") or tool_input.get("file_path") or tool_input.get("path") or ""


# ── State ─────────────────────────────────────────────────────────


def get_state_path(session_id: str) -> Path:
    # Sanitize: session_id comes from the hook payload; keep the filename safe.
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)
    return get_state_dir() / f"{safe}.json"


STATE_DIR_NAME = "totem-hook-state"
STATE_TTL_SECONDS = 24 * 3600


def get_state_dir() -> Path:
    """Private (0700) per-user directory for hook state."""
    path = Path(tempfile.gettempdir()) / STATE_DIR_NAME
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _cleanup_state_dir() -> None:
    """Best-effort TTL sweep so long-dead sessions do not accumulate."""
    now = time.time()
    try:
        entries = list(get_state_dir().iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_file() and now - entry.stat().st_mtime > STATE_TTL_SECONDS:
                entry.unlink()
        except OSError:
            continue


def _normalize_state(state: dict) -> dict:
    """Migrate legacy single-slot gates to per-file maps."""
    if "pending_read" in state:
        legacy = state.pop("pending_read")
        state.setdefault("pending_reads", {})
        if legacy:
            state["pending_reads"][legacy] = True
    if "pending_write" in state:
        legacy = state.pop("pending_write")
        state.setdefault("pending_writes", {})
        if legacy:
            state["pending_writes"][legacy] = True
    state.setdefault("searched", {})
    state.setdefault("pending_reads", {})
    state.setdefault("pending_writes", {})
    state.setdefault("pending_verify", {})
    return state


def load_state(session_id: str) -> dict:
    _cleanup_state_dir()
    path = get_state_path(session_id)
    if path.exists():
        try:
            return _normalize_state(json.loads(path.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    return _normalize_state({})


def save_state(session_id: str, state: dict) -> None:
    """Atomically persist state: temp file + fsync + rename, mode 0600."""
    path = get_state_path(session_id)
    try:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    except OSError:
        return
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(state))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


@contextmanager
def hook_lock(session_id: str):
    """Per-session non-blocking flock.

    Parallel agent tool calls fire this hook concurrently; without the lock
    they pile up totem subprocesses. A call that can't grab the lock fails
    open (allows the tool) instead of queueing behind an in-flight search.
    """
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)
    lock = open(get_state_dir() / f".lock-{safe}", "w")
    try:
        os.chmod(lock.name, 0o600)
    except OSError:
        pass
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
        else:
            yield True
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        except OSError:
            pass
        lock.close()


# ── Memory check ──────────────────────────────────────────────────


def totem_search_items(terms: list[str], project_dir: str, *, types: str | None = None,
                       tags: str | None = None, limit: int = 5) -> list | None:
    """Parsed `totem search` results, or None when the CLI cannot be run.

    Batches terms into a single FTS5 OR query: one subprocess per call.
    """
    clean: list[str] = []
    for term in terms:
        term = FTS5_SPECIAL.sub(" ", term).strip()
        if term and term not in clean:
            clean.append(term)
    if not clean:
        return []
    query = " OR ".join(f'"{t}"' for t in clean[:MAX_SEARCH_TERMS])
    try:
        # --project is a group-level option: it must precede the subcommand.
        cmd = ["totem", "--project", project_dir, "search",
               "--query", query, "--limit", str(limit)]
        if types:
            cmd.extend(["--types", types])
        if tags:
            cmd.extend(["--tags", tags])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return None
        return json.loads(result.stdout) if result.stdout.strip() else []
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return None  # CLI unavailable


def totem_search_any(terms: list[str], project_dir: str, *, types: str | None = None,
                     tags: str | None = None) -> bool:
    """Return True if totem has at least one memory matching ANY term."""
    return bool(totem_search_items(terms, project_dir, types=types, tags=tags, limit=1))


def totem_search(query: str, project_dir: str, *, types: str | None = None,
                 tags: str | None = None) -> bool:
    """Return True if totem has at least one memory matching the query."""
    return totem_search_any([query], project_dir, types=types, tags=tags)


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
        return f"read:{input_path(tool_input)}"
    if tool_name in ("Bash", "bash"):
        return f"bash:{tool_input.get('command', '')}"
    return ""


def has_verify_memory(file_path: str, project_dir: str) -> bool:
    """True when an invariant/constraint tagged verify:<file> exists for the path."""
    base = Path(file_path).name
    try:
        return totem_search_any(
            [base], project_dir, types="invariant,constraint", tags=f"verify:{base}"
        ) or totem_search_any(
            [file_path], project_dir, types="invariant,constraint", tags=f"verify:{file_path}"
        )
    except Exception:
        return False


def verification_recorded(file_path: str, project_dir: str, tags: list) -> bool:
    """True when a read's verify tag is backed by a real verification record.

    Requires a ``verify`` tag and, when the totem CLI is available, a matching
    invariant/constraint with ``verifiedAt`` set. Falls back to tag presence
    when the CLI cannot be run (fail open, like the rest of the hook).
    """
    if not any(str(t).startswith("verify") for t in tags):
        return False
    base = Path(file_path).name
    items: list = []
    for tag in (f"verify:{base}", f"verify:{file_path}"):
        found = totem_search_items(
            [base, file_path], project_dir, types="invariant,constraint",
            tags=tag, limit=5,
        )
        if found is None:
            return True  # CLI unavailable: fall back to tag presence
        items.extend(found)
    if not items:
        return True  # no record surfaced: fall back to tag presence
    return any(item.get("verifiedAt") for item in items)


def has_memory_for(tool_name: str, tool_input: dict, project_dir: str) -> bool:
    """Dispatch the memory check per tool kind."""
    if tool_name in READ_TOOLS:
        file_path = input_path(tool_input)
        if not file_path:
            return False
        # Gate on real file memories only (implementation kind, matching path
        # or its basename, since path separators hurt FTS. One subprocess.
        return totem_search_any([file_path, Path(file_path).name], project_dir,
                                types="implementation")
    if tool_name in ("Grep", "grep", "Glob", "glob"):
        pattern = tool_input.get("pattern", tool_input.get("regex", ""))
        return totem_search_any(tokenize(pattern), project_dir)
    if tool_name in ("Bash", "bash"):
        command = tool_input.get("command", "")
        first = command.strip().split()[0].split("/")[-1].lower() if command.strip() else ""
        if not SUBCMDS.match(first):
            # Plain command: only gate on known cmd: outcomes.
            return bool(first) and totem_search_any([first], project_dir, tags=f"cmd:{first}")
        return totem_search_any(tokenize_bash(command), project_dir)
    return False


# ── Decisions ─────────────────────────────────────────────────────


def deny(reason: str) -> None:
    if ENFORCEMENT in ("off", "warn"):
        # Advisory modes never block; warn surfaces the would-be denial.
        if ENFORCEMENT == "warn":
            print(f"[totem:warn] {reason}", file=sys.stderr)
        sys.exit(0)
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


def _log_fail_open(reason: str) -> None:
    """Record that enforcement could not run (observability)."""
    try:
        path = get_state_dir() / "enforcement.log"
        with open(path, "a") as handle:
            handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} fail_open: {reason}\n")
        os.chmod(path, 0o600)
    except OSError:
        pass


def fail_open(reason: str) -> None:
    """A check could not complete: strict denies, otherwise allow and log."""
    _log_fail_open(reason)
    if ENFORCEMENT == "strict":
        deny(f"totem enforcement could not complete ({reason}); strict mode blocks.")
    allow()


# ── Subcommands ───────────────────────────────────────────────────


def cmd_pre(payload: dict) -> None:
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    project_dir = payload.get("cwd", os.getcwd())
    session_id = payload.get("session_id") or str(os.getppid())

    state = load_state(session_id)

    # 1. Register calls clear their own file's gate (parallel reads stay gated).
    if tool_name == REGISTER_READ:
        path = input_path(tool_input)
        pending_verify = state.get("pending_verify", {})
        if path and pending_verify.get(path):
            tags = tool_input.get("tags") or []
            if not verification_recorded(path, project_dir, tags):
                deny(
                    f"{path} has an invariant/constraint tagged verify. Verify it "
                    f"(call memory_verify_tool on the memory) and register the read "
                    f"with tags including 'verify:{path}'."
                )
            pending_verify.pop(path, None)
        if path:
            state.get("pending_reads", {}).pop(path, None)
        else:
            state["pending_reads"] = {}
        save_state(session_id, state)
        allow()
    if tool_name == REGISTER_WRITE:
        path = input_path(tool_input)
        if path:
            state.get("pending_writes", {}).pop(path, None)
        else:
            state["pending_writes"] = {}
        save_state(session_id, state)
        allow()

    # 2. Commit-gate: pending registrations block all non-totem tools.
    if not tool_name.startswith(MCP_PREFIX):
        pending_reads = state.get("pending_reads") or {}
        if pending_reads:
            files = ", ".join(sorted(pending_reads))
            deny(
                f"You read: {files}. You MUST call register_file_read_tool for "
                f"each with what you learned before doing anything else "
                f"(subject, kind, statement, tags)."
            )
        pending_writes = state.get("pending_writes") or {}
        if pending_writes:
            files = ", ".join(sorted(pending_writes))
            deny(
                f"You modified: {files}. You MUST call register_file_write_tool "
                f"documenting what changed and why before doing anything else."
            )

    # 3. Memory gates for search/read tools.
    if tool_name in READ_TOOLS or tool_name in SEARCH_TOOLS:
        search_key = build_search_key(tool_name, tool_input)
        if not search_key:
            allow()
        if search_key in state.get("searched", {}):
            allow()  # Already blocked once this turn; agent checked memory.
        with hook_lock(session_id) as acquired:
            if not acquired:
                fail_open("lock unavailable")  # another hook invocation is mid-search
            has_memory = has_memory_for(tool_name, tool_input, project_dir)
        if has_memory:
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

    file_path = input_path(tool_input)
    if not file_path:
        allow()

    state = load_state(session_id)
    project_dir = payload.get("cwd", os.getcwd())
    if tool_name in READ_TOOLS:
        state.setdefault("pending_reads", {})[file_path] = True
        if has_verify_memory(file_path, project_dir):
            state.setdefault("pending_verify", {})[file_path] = True
    elif tool_name in WRITE_TOOLS:
        state.setdefault("pending_writes", {})[file_path] = True
    else:
        allow()
    save_state(session_id, state)
    allow()


def cmd_clear(payload: dict) -> None:
    session_id = payload.get("session_id") or str(os.getppid())
    path = get_state_path(session_id)
    if path.exists():
        save_state(
            session_id,
            {
                "searched": {},
                "pending_reads": {},
                "pending_writes": {},
                "pending_verify": {},
            },
        )
    sys.exit(0)


def main() -> None:
    if ENFORCEMENT == "off":
        sys.exit(0)  # policy: never block
    if len(sys.argv) < 2 or sys.argv[1] not in ("pre", "post", "clear"):
        fail_open("unknown usage")
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        fail_open("malformed payload")

    {"pre": cmd_pre, "post": cmd_post, "clear": cmd_clear}[sys.argv[1]](payload)


if __name__ == "__main__":
    main()
