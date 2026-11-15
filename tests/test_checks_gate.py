"""Evidence-derived verification: a recorded check, not a free-text claim."""

from __future__ import annotations

import pytest

from totem_mcp.tools import (
    memory_check_record,
    memory_create,
    memory_verify,
)


def _item(conn, title: str = "g"):
    return memory_create(
        conn, type="gotcha", title=title, statement=f"statement {title}", tags=["t"]
    )["id"]


def test_check_backed_verify_marks_check_mode(fresh_db):
    item_id = _item(fresh_db)
    check = memory_check_record(
        fresh_db, item_id, "pytest tests/x.py", command="pytest", exit_code=0,
        output="1 passed",
    )
    result = memory_verify(fresh_db, item_id, check_id=check["id"])

    assert result["verificationMode"] == "check"
    assert result["confidence"] == 1.0
    assert result["check"]["exitCode"] == 0
    assert result["check"]["outputHash"]


def test_failing_check_cannot_verify(fresh_db):
    item_id = _item(fresh_db)
    check = memory_check_record(fresh_db, item_id, "pytest", exit_code=1)
    with pytest.raises(ValueError, match="did not pass"):
        memory_verify(fresh_db, item_id, check_id=check["id"])


def test_check_from_another_item_is_rejected(fresh_db):
    first = _item(fresh_db)
    second = _item(fresh_db, "other")
    check = memory_check_record(fresh_db, first, "pytest")
    with pytest.raises(ValueError, match="does not belong"):
        memory_verify(fresh_db, second, check_id=check["id"])


def test_check_against_different_evidence_is_rejected(fresh_db):
    item_id = _item(fresh_db)
    check = memory_check_record(
        fresh_db, item_id, "pytest", evidence_hash="deadbeef"
    )
    with pytest.raises(ValueError, match="different evidence"):
        memory_verify(fresh_db, item_id, check_id=check["id"])


def test_assertion_only_verify_is_flagged(fresh_db):
    item_id = _item(fresh_db)
    result = memory_verify(fresh_db, item_id, method="I looked at it")
    assert result["verificationMode"] == "assertion"
    assert any("assertion-only" in w for w in result["warnings"])


def test_unknown_check_is_rejected(fresh_db):
    item_id = _item(fresh_db)
    with pytest.raises(ValueError, match="check not found"):
        memory_verify(fresh_db, item_id, check_id="nope")


def test_export_and_import_preserve_checks(fresh_db, tmp_path):
    from totem_mcp.db import connect, init_db
    from totem_mcp.tools import memory_export, memory_import

    item_id = _item(fresh_db)
    check = memory_check_record(fresh_db, item_id, "pytest", exit_code=0)
    export = memory_export(fresh_db)
    assert export["format_version"] == 2
    assert export["checks"][0]["id"] == check["id"]

    other = connect(db_path=tmp_path / "other-v2.db")
    init_db(other)
    report = memory_import(other, export)
    assert report["checks_imported"] == 1
    row = other.execute(
        "SELECT name, exit_code FROM memory_checks WHERE id = ?", (check["id"],)
    ).fetchone()
    assert row == ("pytest", 0)
