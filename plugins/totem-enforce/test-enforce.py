#!/usr/bin/env python3
"""Tests for the unified totem enforcement hook (hooks/totem-hook.py)."""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Load hooks/totem-hook.py (hyphenated name → importlib)
HOOK_PATH = Path(__file__).parent / "hooks" / "totem-hook.py"
spec = importlib.util.spec_from_file_location("totem_hook", HOOK_PATH)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


def run_cmd(fn, payload):
    """Run a hook subcommand, returning (exit_code, stdout)."""
    out = io.StringIO()
    code = None
    with contextlib.redirect_stdout(out):
        try:
            fn(payload)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue()


def run_denied(fn, payload):
    """Return the deny reason, or None if the call was allowed."""
    _, out = run_cmd(fn, payload)
    if not out.strip():
        return None
    decision = json.loads(out)
    return decision["hookSpecificOutput"]["permissionDecisionReason"]


class TestTokenizeBash(unittest.TestCase):
    def test_simple_command(self):
        self.assertEqual(hook.tokenize_bash("uvx totem-mcp"), ["uvx"])

    def test_short_command_filtered(self):
        self.assertEqual(hook.tokenize_bash("ls -la"), [])

    def test_grep_subcommand(self):
        result = hook.tokenize_bash("grep -r MemoryType src/")
        self.assertIn("memorytype", result)
        self.assertIn("src", result)

    def test_cd_prefix_stripped(self):
        self.assertIn("foo", hook.tokenize_bash("cd src && grep -r Foo ."))

    def test_non_subcommand_uses_first_word(self):
        self.assertEqual(hook.tokenize_bash("python3 script.py --arg value"), ["python3"])

    def test_empty_command(self):
        self.assertEqual(hook.tokenize_bash(""), [])

    def test_stop_words_filtered(self):
        result = hook.tokenize_bash("grep this and that for the project")
        self.assertIn("project", result)
        self.assertNotIn("this", result)


class TestSubcmdsRegex(unittest.TestCase):
    def test_all_subcommands_match(self):
        for c in ["grep", "find", "cat", "head", "tail", "wc", "sort", "uniq",
                  "awk", "sed", "less", "more", "diff", "comm", "xargs", "file",
                  "rg", "ag", "ack", "jq"]:
            self.assertTrue(hook.SUBCMDS.match(c), f"{c} should match SUBCMDS")

    def test_non_subcommands_dont_match(self):
        for c in ["python", "node", "uvx", "totem", "git"]:
            self.assertIsNone(hook.SUBCMDS.match(c), f"{c} should not match SUBCMDS")


class TestBuildSearchKey(unittest.TestCase):
    def test_grep_tool(self):
        self.assertEqual(hook.build_search_key("Grep", {"pattern": "MemoryType"}), "grep:MemoryType")

    def test_read_tool(self):
        self.assertEqual(hook.build_search_key("Read", {"filePath": "/a/b.py"}), "read:/a/b.py")

    def test_bash_tool(self):
        self.assertEqual(hook.build_search_key("Bash", {"command": "ls"}), "bash:ls")

    def test_unknown_tool(self):
        self.assertEqual(hook.build_search_key("Edit", {"filePath": "x.py"}), "")


class TestTotemSearch(unittest.TestCase):
    @patch.object(hook.subprocess, "run")
    def test_true_when_memory_exists(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='[{"id": "1"}]')
        self.assertTrue(hook.totem_search("foo", "/project"))

    @patch.object(hook.subprocess, "run")
    def test_false_when_empty(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="[]")
        self.assertFalse(hook.totem_search("foo", "/project"))

    @patch.object(hook.subprocess, "run")
    def test_false_on_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        self.assertFalse(hook.totem_search("foo", "/project"))

    @patch.object(hook.subprocess, "run")
    def test_project_flag_precedes_subcommand(self, mock_run):
        """--project is a group option: `totem --project DIR search ...`."""
        mock_run.return_value = MagicMock(returncode=0, stdout="[]")
        hook.totem_search("foo", "/project")
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[:4], ["totem", "--project", "/project", "search"])


class GateTestCase(unittest.TestCase):
    """Base: isolate state per test via a temp session id."""

    def setUp(self):
        self.session = f"test-{id(self)}"
        self.payload_base = {"session_id": self.session, "cwd": "/project"}
        # Ensure clean state
        run_cmd(hook.cmd_clear, dict(self.payload_base))

    def tearDown(self):
        hook.get_state_path(self.session).unlink(missing_ok=True)

    def payload(self, tool_name, tool_input):
        return {**self.payload_base, "tool_name": tool_name, "tool_input": tool_input}


class TestReadGate(GateTestCase):
    @patch.object(hook, "totem_search", return_value=True)
    def test_read_denied_when_memory_exists(self, _):
        reason = run_denied(hook.cmd_pre, self.payload("Read", {"filePath": "/a/b.py"}))
        self.assertIsNotNone(reason)
        self.assertIn("engineering_context_tool", reason)

    @patch.object(hook, "totem_search", return_value=True)
    def test_read_retry_allowed_after_block(self, _):
        p = self.payload("Read", {"filePath": "/a/b.py"})
        self.assertIsNotNone(run_denied(hook.cmd_pre, p))
        self.assertIsNone(run_denied(hook.cmd_pre, p))  # retry allowed

    @patch.object(hook, "totem_search", return_value=False)
    def test_read_allowed_when_no_memory(self, _):
        self.assertIsNone(run_denied(hook.cmd_pre, self.payload("Read", {"filePath": "/a/b.py"})))

    @patch.object(hook, "totem_search", return_value=True)
    def test_grep_denied_when_memory_exists(self, _):
        reason = run_denied(hook.cmd_pre, self.payload("Grep", {"pattern": "MemoryType"}))
        self.assertIsNotNone(reason)
        self.assertIn("memory_search_tool", reason)


class TestReadCommitGate(GateTestCase):
    def test_post_read_arms_gate_and_blocks_other_tools(self):
        run_cmd(hook.cmd_post, self.payload("Read", {"filePath": "/a/b.py"}))
        reason = run_denied(hook.cmd_pre, self.payload("Bash", {"command": "ls"}))
        self.assertIsNotNone(reason)
        self.assertIn("register_file_read_tool", reason)
        self.assertIn("/a/b.py", reason)

    def test_gate_cleared_by_register_read(self):
        run_cmd(hook.cmd_post, self.payload("Read", {"filePath": "/a/b.py"}))
        run_cmd(hook.cmd_pre, self.payload("mcp__totem__register_file_read_tool", {}))
        with patch.object(hook, "totem_search", return_value=False):
            self.assertIsNone(run_denied(hook.cmd_pre, self.payload("Bash", {"command": "ls"})))

    def test_other_totem_tools_allowed_while_gated(self):
        run_cmd(hook.cmd_post, self.payload("Read", {"filePath": "/a/b.py"}))
        self.assertIsNone(
            run_denied(hook.cmd_pre, self.payload("mcp__totem__memory_search_tool", {"query": "x"}))
        )


class TestWriteCommitGate(GateTestCase):
    def test_post_write_arms_gate(self):
        run_cmd(hook.cmd_post, self.payload("Write", {"filePath": "/a/c.py"}))
        reason = run_denied(hook.cmd_pre, self.payload("Read", {"filePath": "/a/b.py"}))
        self.assertIsNotNone(reason)
        self.assertIn("register_file_write_tool", reason)

    def test_edit_arms_gate(self):
        run_cmd(hook.cmd_post, self.payload("Edit", {"filePath": "/a/c.py"}))
        reason = run_denied(hook.cmd_pre, self.payload("Grep", {"pattern": "foo"}))
        self.assertIsNotNone(reason)
        self.assertIn("register_file_write_tool", reason)

    def test_gate_cleared_by_register_write(self):
        run_cmd(hook.cmd_post, self.payload("Write", {"filePath": "/a/c.py"}))
        run_cmd(hook.cmd_pre, self.payload("mcp__totem__register_file_write_tool", {}))
        with patch.object(hook, "totem_search", return_value=False):
            self.assertIsNone(run_denied(hook.cmd_pre, self.payload("Bash", {"command": "ls"})))


class TestClear(GateTestCase):
    def test_clear_resets_gates_and_searched(self):
        with patch.object(hook, "totem_search", return_value=True):
            p = self.payload("Read", {"filePath": "/a/b.py"})
            self.assertIsNotNone(run_denied(hook.cmd_pre, p))  # blocked, recorded
        run_cmd(hook.cmd_post, self.payload("Write", {"filePath": "/a/c.py"}))
        run_cmd(hook.cmd_clear, dict(self.payload_base))
        state = hook.load_state(self.session)
        self.assertEqual(state, {"searched": {}, "pending_read": None, "pending_write": None})


class TestFailOpen(GateTestCase):
    def test_unknown_tool_allowed(self):
        self.assertIsNone(run_denied(hook.cmd_pre, self.payload("WebSearch", {"query": "x"})))

    def test_post_ignores_unknown_tools(self):
        run_cmd(hook.cmd_post, self.payload("Bash", {"command": "ls"}))
        state = hook.load_state(self.session)
        self.assertIsNone(state["pending_read"])
        self.assertIsNone(state["pending_write"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
