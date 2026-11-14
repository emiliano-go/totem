"""The write gate records the git-observed change set, not just a claim."""

from __future__ import annotations

import subprocess

from totem_mcp.tools import _git_change_snapshot, register_file_write


def _git(root, *args):
    subprocess.run(
        ["git", "-C", str(root), *args], check=True,
        capture_output=True, text=True,
    )


def test_snapshot_lists_modified_and_untracked_files(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    tracked = tmp_path / "tracked.py"
    tracked.write_text("one\n")
    _git(tmp_path, "add", "tracked.py")
    _git(tmp_path, "commit", "-m", "init")

    tracked.write_text("two\n")
    (tmp_path / "new.py").write_text("new\n")
    snapshot = _git_change_snapshot(tmp_path)

    assert "tracked.py" in snapshot["actualChangedFiles"]
    assert "new.py" in snapshot["actualChangedFiles"]
    assert len(snapshot["diffDigest"]) == 64


def test_write_registration_records_snapshot(fresh_db, tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    target = tmp_path / "mod.py"
    target.write_text("def f():\n    return 1\n")

    result = register_file_write(
        fresh_db, path=str(target), statement="f returns 1",
        reason="add f", tags=["x"],
    )

    assert result["action"] == "created"
    assert "mod.py" in result["actualChangedFiles"]
    assert len(result["diffDigest"]) == 64
