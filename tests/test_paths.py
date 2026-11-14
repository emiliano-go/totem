"""Evidence path containment."""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from totem_mcp.models import Evidence
from totem_mcp.paths import EvidencePathError, resolve_within
from totem_mcp.tools import register_file_read


def test_relative_path_stays_under_root(tmp_path):
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "a.py"
    target.write_text("x")
    assert resolve_within(tmp_path, "src/a.py") == target.resolve()


@pytest.mark.parametrize("bad", ["../escape.py", "src/../../escape.py", ".."])
def test_parent_traversal_is_rejected(tmp_path, bad):
    with pytest.raises(EvidencePathError):
        resolve_within(tmp_path, bad)


def test_sibling_prefix_escape_is_rejected(tmp_path):
    root = tmp_path / "repo"
    sibling = tmp_path / "repo-escape"
    root.mkdir()
    sibling.mkdir()
    (sibling / "x.py").write_text("x")
    with pytest.raises(EvidencePathError):
        resolve_within(root, str(sibling / "x.py"))


def test_symlink_escape_is_rejected(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "x.py").write_text("x")
    os.symlink(outside / "x.py", root / "link.py")
    with pytest.raises(EvidencePathError):
        resolve_within(root, "link.py")


def test_register_rejects_outside_file(fresh_db, tmp_path):
    outside = tmp_path.parent / "outside.py"
    outside.write_text("secret\n")
    result = register_file_read(
        fresh_db, path=str(outside), statement="x", subject="x",
        kind="module", tags=["x"],
    )
    assert "escapes project root" in result["error"]


def test_evidence_model_rejects_traversal():
    with pytest.raises(ValidationError):
        Evidence(
            path="../secret.py", startLine=1, endLine=1,
            contentHash="0" * 64, capturedAt="2026-01-01T00:00:00+00:00",
        )
