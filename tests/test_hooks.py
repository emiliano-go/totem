"""Enforcement hook: per-file gates, parallel reads, verify-tag requirement."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HOOK = Path(__file__).parents[1] / "plugins" / "totem-enforce" / "hooks" / "totem-hook.py"
REGISTER_READ = "mcp__totem__register_file_read_tool"
REGISTER_WRITE = "mcp__totem__register_file_write_tool"


def run_hook(sub: str, payload: dict, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK), sub],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, **(env or {})},
    )


def state_path(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in session_id)
    directory = Path(tempfile.gettempdir()) / "totem-hook-state"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{safe}.json"


@pytest.fixture
def session_id():
    sid = f"pytest-hook-{os.getpid()}"
    state_path(sid).unlink(missing_ok=True)
    yield sid
    state_path(sid).unlink(missing_ok=True)


def _decision(proc: subprocess.CompletedProcess) -> dict | None:
    if not proc.stdout.strip():
        return None
    return json.loads(proc.stdout)["hookSpecificOutput"]


def test_parallel_reads_gate_per_file(session_id):
    for path in ("/tmp/a.py", "/tmp/b.py"):
        run_hook("post", {
            "tool_name": "Read", "tool_input": {"file_path": path},
            "session_id": session_id, "cwd": "/tmp",
        })

    blocked = _decision(run_hook("pre", {
        "tool_name": "Bash", "tool_input": {"command": "ls"},
        "session_id": session_id, "cwd": "/tmp",
    }))
    assert blocked["permissionDecision"] == "deny"
    assert "/tmp/a.py" in blocked["permissionDecisionReason"]
    assert "/tmp/b.py" in blocked["permissionDecisionReason"]

    # registering one file clears only that file's gate
    run_hook("pre", {
        "tool_name": REGISTER_READ, "tool_input": {"path": "/tmp/a.py"},
        "session_id": session_id, "cwd": "/tmp",
    })
    blocked = _decision(run_hook("pre", {
        "tool_name": "Bash", "tool_input": {"command": "ls"},
        "session_id": session_id, "cwd": "/tmp",
    }))
    assert "/tmp/b.py" in blocked["permissionDecisionReason"]
    assert "/tmp/a.py" not in blocked["permissionDecisionReason"]

    run_hook("pre", {
        "tool_name": REGISTER_READ, "tool_input": {"path": "/tmp/b.py"},
        "session_id": session_id, "cwd": "/tmp",
    })
    assert _decision(run_hook("pre", {
        "tool_name": "Bash", "tool_input": {"command": "ls"},
        "session_id": session_id, "cwd": "/tmp",
    })) is None  # allowed


def test_verify_tag_required_after_read(session_id):
    state_path(session_id).write_text(json.dumps({
        "searched": {},
        "pending_reads": {"/tmp/v.py": True},
        "pending_writes": {},
        "pending_verify": {"/tmp/v.py": True},
    }))

    denied = _decision(run_hook("pre", {
        "tool_name": REGISTER_READ,
        "tool_input": {"path": "/tmp/v.py", "tags": ["x"]},
        "session_id": session_id, "cwd": "/tmp",
    }))
    assert denied["permissionDecision"] == "deny"
    assert "verify" in denied["permissionDecisionReason"]

    allowed = run_hook("pre", {
        "tool_name": REGISTER_READ,
        "tool_input": {"path": "/tmp/v.py", "tags": ["verify:/tmp/v.py"]},
        "session_id": session_id, "cwd": "/tmp",
    })
    assert _decision(allowed) is None
    state = json.loads(state_path(session_id).read_text())
    assert state["pending_verify"] == {} and state["pending_reads"] == {}


def test_write_gate_and_legacy_state_migration(session_id):
    # legacy single-slot state must migrate to the per-file map
    state_path(session_id).write_text(json.dumps({
        "searched": {}, "pending_read": "/tmp/legacy.py", "pending_write": None,
    }))
    blocked = _decision(run_hook("pre", {
        "tool_name": "Bash", "tool_input": {"command": "ls"},
        "session_id": session_id, "cwd": "/tmp",
    }))
    assert "/tmp/legacy.py" in blocked["permissionDecisionReason"]

    run_hook("pre", {
        "tool_name": REGISTER_READ, "tool_input": {"path": "/tmp/legacy.py"},
        "session_id": session_id, "cwd": "/tmp",
    })
    run_hook("post", {
        "tool_name": "Edit", "tool_input": {"file_path": "/tmp/legacy.py"},
        "session_id": session_id, "cwd": "/tmp",
    })
    blocked = _decision(run_hook("pre", {
        "tool_name": "Bash", "tool_input": {"command": "ls"},
        "session_id": session_id, "cwd": "/tmp",
    }))
    assert "modified" in blocked["permissionDecisionReason"].lower()


def test_state_file_is_private(session_id):
    run_hook("post", {
        "tool_name": "Read", "tool_input": {"file_path": "/tmp/priv.py"},
        "session_id": session_id, "cwd": "/tmp",
    })
    path = state_path(session_id)
    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600
    assert path.parent.name == "totem-hook-state"
    assert (path.parent.stat().st_mode & 0o777) == 0o700


def test_state_write_is_atomic_and_valid_json(session_id):
    for path in ("/tmp/x.py", "/tmp/y.py", "/tmp/z.py"):
        run_hook("post", {
            "tool_name": "Read", "tool_input": {"file_path": path},
            "session_id": session_id, "cwd": "/tmp",
        })
    # the persisted state parses and holds all three gates (no torn writes)
    state = json.loads(state_path(session_id).read_text())
    assert {"/tmp/x.py", "/tmp/y.py", "/tmp/z.py"} <= set(state["pending_reads"])
    # no leftover temp files in the state dir
    leftovers = [p.name for p in state_path(session_id).parent.glob(".tmp-*")]
    assert leftovers == []


def _arm_read_gate(session_id):
    run_hook("post", {
        "tool_name": "Read", "tool_input": {"file_path": "/tmp/gate.py"},
        "session_id": session_id, "cwd": "/tmp",
    })


def test_enforcement_off_never_blocks(session_id):
    _arm_read_gate(session_id)
    proc = run_hook("pre", {
        "tool_name": "Bash", "tool_input": {"command": "ls"},
        "session_id": session_id, "cwd": "/tmp",
    }, env={"TOTEM_ENFORCEMENT": "off"})
    assert _decision(proc) is None  # allowed


def test_enforcement_warn_allows_and_warns(session_id):
    _arm_read_gate(session_id)
    proc = run_hook("pre", {
        "tool_name": "Bash", "tool_input": {"command": "ls"},
        "session_id": session_id, "cwd": "/tmp",
    }, env={"TOTEM_ENFORCEMENT": "warn"})
    assert _decision(proc) is None  # advisory: does not block
    assert "totem:warn" in proc.stderr


def test_enforcement_strict_fails_closed_on_bad_payload():
    proc = subprocess.run(
        [sys.executable, str(HOOK), "pre"],
        input="{not valid json",
        capture_output=True,
        text=True,
        env={**os.environ, "TOTEM_ENFORCEMENT": "strict"},
    )
    assert "deny" in proc.stdout
