"""Verification: verified_at/verified_commit record that a memory was checked."""

from __future__ import annotations

from totem_mcp.db import get_item
from totem_mcp.tools import (
    memory_create,
    memory_delete,
    memory_get,
    memory_revalidate,
    memory_verify,
    register_file_read,
)


def _item(conn, title="claim"):
    return memory_create(
        conn, type="gotcha", title=title, statement=f"claim {title}", tags=["v"]
    )["id"]


def test_verify_sets_fields_and_confidence(fresh_db):
    item_id = _item(fresh_db)

    result = memory_verify(fresh_db, item_id, method="pytest", commit="abc123")

    stored = get_item(fresh_db, item_id)
    assert stored.verified_at is not None
    assert stored.verified_commit == "abc123"
    assert stored.confidence == 1.0
    assert result["verifiedAt"] == stored.verified_at


def test_verify_defaults_commit_to_head_or_none(fresh_db):
    item_id = _item(fresh_db)
    result = memory_verify(fresh_db, item_id)
    assert result["verifiedAt"] is not None
    assert "verifiedCommit" in result  # None outside a repo, sha inside


def test_verify_records_history(fresh_db):
    item_id = _item(fresh_db)

    memory_verify(fresh_db, item_id, method="ran the suite", commit="deadbeef")

    rows = fresh_db.execute(
        "SELECT event, field, new_value FROM memory_history "
        "WHERE item_id = ? AND event = 'verified'",
        (item_id,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][2] == "ran the suite"


def test_verify_can_link_verification_artifact(fresh_db):
    item_id = _item(fresh_db, "claim")
    artifact = _item(fresh_db, "test result")

    memory_verify(fresh_db, item_id, verified_by_id=artifact)

    rel = fresh_db.execute(
        "SELECT from_id, to_id, kind FROM memory_relations WHERE kind = 'verified_by'"
    ).fetchone()
    assert rel == (item_id, artifact, "verified_by")


def test_verify_confidence_override(fresh_db):
    item_id = _item(fresh_db)
    result = memory_verify(fresh_db, item_id, confidence=0.75)
    assert result["confidence"] == 0.75


def test_verify_missing_and_deleted(fresh_db):
    assert memory_verify(fresh_db, "nope") is None

    item_id = _item(fresh_db)
    memory_delete(fresh_db, item_id, "obsolete")
    # get_item hides deleted rows, so a deleted item reads as not found.
    assert memory_verify(fresh_db, item_id) is None


def _verified_file(conn, sample_file):
    item = register_file_read(
        conn, path=str(sample_file), statement="fact", subject="s",
        kind="function", tags=["v"],
    )
    memory_verify(conn, item["id"], commit="abc")
    return item["id"]


def test_get_voids_verification_when_evidence_changes(fresh_db, sample_file):
    item_id = _verified_file(fresh_db, sample_file)
    sample_file.write_text("totally different content\n")

    result = memory_get(fresh_db, item_id)

    assert result["verifiedAt"] is None
    assert result["confidence"] == 0.6  # reset to agent provenance default
    assert any("Verification voided" in w for w in result.get("warnings", []))


def test_fresh_verification_survives_get(fresh_db, sample_file):
    item_id = _verified_file(fresh_db, sample_file)

    result = memory_get(fresh_db, item_id)

    assert result["verifiedAt"] is not None
    assert result["confidence"] == 1.0


def test_revalidate_dry_run_then_apply(fresh_db, sample_file):
    item_id = _verified_file(fresh_db, sample_file)
    sample_file.write_text("changed again\n")

    dry = memory_revalidate(fresh_db, dry_run=True)
    assert dry["count"] == 1 and item_id in dry["voided"]
    assert get_item(fresh_db, item_id).verified_at is not None  # untouched

    applied = memory_revalidate(fresh_db, dry_run=False)
    assert applied["count"] == 1
    assert get_item(fresh_db, item_id).verified_at is None
    assert memory_revalidate(fresh_db, dry_run=False)["count"] == 0
