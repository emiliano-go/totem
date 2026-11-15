from pathlib import Path

from totem_mcp.db import get_item
from totem_mcp.hashing import check_staleness
from totem_mcp.tools import (
    memory_evidence_refresh,
    register_file_read,
)


def test_refresh_rehashes_changed_file(fresh_db, tmp_path):
    target = tmp_path / "auth.py"
    target.write_text("def validate():\n    return True\n")
    registered = register_file_read(
        fresh_db, path=str(target), statement="validate checks the token",
        subject="validate", kind="function", tags=["auth"],
    )
    target.write_text("def validate():\n    return False\n")

    preview = memory_evidence_refresh(fresh_db, dry_run=True)
    assert registered["id"] in preview["refreshed"]
    applied = memory_evidence_refresh(fresh_db, dry_run=False)
    assert registered["id"] in applied["refreshed"]

    item = get_item(fresh_db, registered["id"])
    assert not item.evidence[0].content_hash == registered["evidence"]["contentHash"]
    assert not check_staleness(
        Path(item.evidence[0].path),
        item.evidence[0].start_line,
        item.evidence[0].end_line,
        item.evidence[0].content_hash,
        symbol=item.evidence[0].symbol,
        blob_hash=item.evidence[0].blob_hash,
        symbol_hash=item.evidence[0].symbol_hash,
    )


def test_refresh_reports_orphaned_evidence(fresh_db, tmp_path):
    target = tmp_path / "gone.py"
    target.write_text("def gone():\n    return 1\n")
    registered = register_file_read(
        fresh_db, path=str(target), statement="gone returns one",
        subject="gone", kind="function", tags=["gone"],
    )
    target.unlink()
    report = memory_evidence_refresh(fresh_db, dry_run=False)
    assert any(
        entry["id"] == registered["id"] for entry in report["orphaned"]
    )
    stored = get_item(fresh_db, registered["id"])
    assert stored.evidence


def test_refresh_repairs_unique_orphan_suffix(fresh_db, tmp_path):
    from totem_mcp.paths import project_root

    root = Path(project_root(fresh_db))
    old = root / ".pr-worktrees" / "branch" / "tests" / "test_alert.py"
    old.parent.mkdir(parents=True)
    old.write_text("def test_alert():\n    assert True\n")
    registered = register_file_read(
        fresh_db, path=str(old), statement="alert test passes",
        subject="test_alert", kind="function", tags=["test"],
    )
    old.unlink()
    current = root / "tests" / "test_alert.py"
    current.parent.mkdir(parents=True)
    current.write_text("def test_alert():\n    assert True\n")

    preview = memory_evidence_refresh(fresh_db, dry_run=True, repair_orphans=True)
    entry = next(
        item for item in preview["orphaned"] if item["id"] == registered["id"]
    )
    assert entry["replacement"].endswith("tests/test_alert.py")
    memory_evidence_refresh(
        fresh_db, dry_run=False, repair_orphans=True,
    )
    stored = get_item(fresh_db, registered["id"])
    assert stored.evidence[0].path.endswith("tests/test_alert.py")
