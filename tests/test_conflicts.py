from totem_mcp.db import get_all_conflicts
from totem_mcp.tools import memory_create


def _evidence(path: str, digest: str) -> list[dict]:
    return [{
        "path": path, "startLine": 1, "endLine": 2, "contentHash": digest,
        "capturedAt": "2026-01-01T00:00:00+00:00",
    }]


def test_overlapping_evidence_with_different_subjects_is_not_a_conflict(fresh_db, tmp_path):
    path = tmp_path / "ledger.py"
    path.write_text("def ledger():\n    return 1\n")
    digest = "0" * 64
    memory_create(
        fresh_db, type="contract", title="BFP definition", statement="BFP is a benign false positive.",
        tags=["ledger"], evidence=_evidence(str(path), digest), metadata={"subject": "bfp"},
    )
    memory_create(
        fresh_db, type="contract", title="MFP definition", statement="MFP is a malicious false positive.",
        tags=["ledger"], evidence=_evidence(str(path), digest), metadata={"subject": "mfp"},
    )
    assert get_all_conflicts(fresh_db) == []


def test_overlapping_evidence_with_same_subject_is_a_conflict(fresh_db, tmp_path):
    path = tmp_path / "ledger.py"
    path.write_text("def ledger():\n    return 1\n")
    digest = "0" * 64
    memory_create(
        fresh_db, type="contract", title="BFP definition", statement="BFP is a benign false positive.",
        tags=["ledger"], evidence=_evidence(str(path), digest), metadata={"subject": "bfp"},
    )
    memory_create(
        fresh_db, type="contract", title="BFP definition v2", statement="BFP is never a benign false positive.",
        tags=["ledger"], evidence=_evidence(str(path), digest), metadata={"subject": "bfp"},
    )
    assert len(get_all_conflicts(fresh_db)) == 1
