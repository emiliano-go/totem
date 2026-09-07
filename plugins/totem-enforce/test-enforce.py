#!/usr/bin/env python3
"""Tests for totem enforcement hook (enforce.py)."""

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent))
from enforce import tokenize_bash, check_memory, build_search_key, SUBCMDS, STOP_WORDS


class TestTokenizeBash(unittest.TestCase):
    """Tests for tokenize_bash function."""

    def test_simple_command(self):
        self.assertEqual(tokenize_bash("uvx totem-mcp"), ["uvx"])

    def test_short_command_filtered(self):
        self.assertEqual(tokenize_bash("ls -la"), [])

    def test_grep_subcommand(self):
        result = tokenize_bash("grep -r MemoryType src/")
        self.assertIn("memorytype", result)
        self.assertIn("src", result)

    def test_find_subcommand(self):
        result = tokenize_bash("find . -name 'test_module' -type f")
        self.assertIn("find", result)
        self.assertIn("test", result)
        self.assertIn("module", result)

    def test_cat_subcommand(self):
        result = tokenize_bash("cat README.md | head -20")
        self.assertIn("readme", result)
        self.assertIn("head", result)

    def test_sed_subcommand(self):
        result = tokenize_bash("sed -i 's/old/new/g' file.txt")
        self.assertIn("sed", result)
        self.assertIn("old", result)
        self.assertIn("new", result)

    def test_cd_prefix_stripped(self):
        result = tokenize_bash("cd src && grep -r Foo .")
        self.assertIn("foo", result)

    def test_semicolon_chain_stripped(self):
        result = tokenize_bash("cd src ; grep -r Foo .")
        self.assertIn("foo", result)

    def test_non_subcommand_uses_first_word(self):
        result = tokenize_bash("python3 script.py --arg value")
        self.assertEqual(result, ["python3"])

    def test_totem_command(self):
        result = tokenize_bash("totem search --query foo")
        self.assertEqual(result, ["totem"])

    def test_empty_command(self):
        self.assertEqual(tokenize_bash(""), [])

    def test_stop_words_filtered(self):
        result = tokenize_bash("grep this and that for the project")
        self.assertIn("project", result)
        self.assertNotIn("this", result)
        self.assertNotIn("and", result)

    def test_deduplication(self):
        result = tokenize_bash("grep -r config config/")
        self.assertEqual(result.count("config"), 1)

    def test_rg_subcommand(self):
        result = tokenize_bash("rg 'pattern' src/")
        self.assertIn("pattern", result)
        self.assertIn("src", result)

    def test_jq_subcommand(self):
        result = tokenize_bash("jq '.username' data.json")
        self.assertIn("username", result)
        self.assertIn("data", result)


class TestSubcmdsRegex(unittest.TestCase):
    """Tests for SUBCMDS regex."""

    def test_all_subcommands_match(self):
        cmds = [
            "grep", "find", "cat", "head", "tail", "wc", "sort", "uniq",
            "awk", "sed", "less", "more", "diff", "comm", "xargs", "file",
            "rg", "ag", "ack", "jq",
        ]
        for c in cmds:
            self.assertTrue(SUBCMDS.match(c), f"{c} should match SUBCMDS")

    def test_non_subcommands_dont_match(self):
        for c in ["python", "node", "uvx", "totem", "git", "cargo", "pip"]:
            self.assertIsNone(SUBCMDS.match(c), f"{c} should not match SUBCMDS")


class TestBuildSearchKey(unittest.TestCase):
    """Tests for build_search_key function."""

    def test_grep_tool(self):
        self.assertEqual(
            build_search_key("Grep", {"pattern": "MemoryType"}),
            "grep:MemoryType",
        )

    def test_grep_lowercase(self):
        self.assertEqual(
            build_search_key("grep", {"pattern": "foo"}),
            "grep:foo",
        )

    def test_grep_regex_field(self):
        self.assertEqual(
            build_search_key("Grep", {"regex": ".*test.*"}),
            "grep:.*test.*",
        )

    def test_glob_tool(self):
        self.assertEqual(
            build_search_key("Glob", {"pattern": "*.py"}),
            "glob:*.py",
        )

    def test_read_tool(self):
        self.assertEqual(
            build_search_key("Read", {"filePath": "/path/to/file.py"}),
            "read:/path/to/file.py",
        )

    def test_bash_tool(self):
        self.assertEqual(
            build_search_key("Bash", {"command": "grep -r foo ."}),
            "bash:grep -r foo .",
        )

    def test_unknown_tool(self):
        self.assertEqual(build_search_key("Edit", {"filePath": "x.py"}), "")

    def test_missing_fields(self):
        self.assertEqual(build_search_key("Grep", {}), "grep:")
        self.assertEqual(build_search_key("Read", {}), "read:")


class TestCheckMemory(unittest.TestCase):
    """Tests for check_memory function."""

    @patch("enforce.subprocess.run")
    def test_returns_true_when_memory_exists(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='[{"id": "1", "title": "test"}]',
        )
        self.assertTrue(check_memory("foo", "/project"))

    @patch("enforce.subprocess.run")
    def test_returns_false_when_no_memory(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="[]",
        )
        self.assertFalse(check_memory("nonexistent", "/project"))

    @patch("enforce.subprocess.run")
    def test_returns_false_on_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        self.assertFalse(check_memory("foo", "/project"))

    @patch("enforce.subprocess.run")
    def test_returns_false_on_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="totem", timeout=5)
        self.assertFalse(check_memory("foo", "/project"))

    @patch("enforce.subprocess.run")
    def test_returns_false_on_bad_json(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json")
        self.assertFalse(check_memory("foo", "/project"))

    @patch("enforce.subprocess.run")
    def test_cmd_tag_used_when_specified(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="[]")
        check_memory("totem", "/project", use_cmd_tag=True)
        call_args = mock_run.call_args[0][0]
        self.assertIn("--tags", call_args)
        self.assertIn("cmd:totem", call_args)

    @patch("enforce.subprocess.run")
    def test_no_cmd_tag_when_disabled(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="[]")
        check_memory("totem", "/project", use_cmd_tag=False)
        call_args = mock_run.call_args[0][0]
        self.assertNotIn("--tags", call_args)


class TestStateManagement(unittest.TestCase):
    """Tests for hook state management."""

    def test_load_state_returns_empty_when_no_file(self):
        from enforce import load_state
        with patch("enforce.get_state_path") as mock_path:
            mock_path.return_value = Path("/nonexistent/state.json")
            state = load_state()
            self.assertEqual(state, {"searched_this_turn": {}})

    def test_save_and_load_state(self):
        from enforce import load_state, save_state
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = Path(f.name)
        try:
            with patch("enforce.get_state_path", return_value=path):
                save_state({"searched_this_turn": {"grep:foo": "now"}})
                state = load_state()
                self.assertEqual(state["searched_this_turn"]["grep:foo"], "now")
        finally:
            path.unlink(missing_ok=True)

    def test_load_state_handles_corrupt_json(self):
        from enforce import load_state
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            f.write("not json {{{")
            path = Path(f.name)
        try:
            with patch("enforce.get_state_path", return_value=path):
                state = load_state()
                self.assertEqual(state, {"searched_this_turn": {}})
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
