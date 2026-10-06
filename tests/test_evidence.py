"""P2 evidence: per-fact implementation memories, structural staleness, activation."""

from __future__ import annotations

from pathlib import Path

from totem_mcp.context import engineering_context
from totem_mcp.hashing import check_staleness, hash_content
from totem_mcp.tools import memory_create, memory_relate, register_file_read


def test_multi_fact_file_creates_distinct_memories(fresh_db, tmp_path):
    target = tmp_path / "auth.py"
    target.write_text("def validate_token():\n    return True\n\n\ndef rotate():\n    return 1\n")

    first = register_file_read(
        fresh_db, path=str(target), statement="validate_token checks the JWT",
        subject="validate_token", kind="api", tags=["auth"],
    )
    second = register_file_read(
        fresh_db, path=str(target), statement="rotate rotates refresh tokens",
        subject="rotate", kind="function", tags=["auth"],
    )
    assert first["action"] == "created"
    assert second["action"] == "created"
    assert first["id"] != second["id"]

    again = register_file_read(
        fresh_db, path=str(target), statement="validate_token verifies the JWT signature",
        subject="validate_token", kind="api", tags=["auth"],
    )
    assert again["action"] == "updated"
    assert again["id"] == first["id"]


def test_symbol_survives_moved_code(fresh_db, tmp_path):
    target = tmp_path / "mod.py"
    target.write_text("def handler():\n    return 'ok'\n")
    registered = register_file_read(
        fresh_db, path=str(target), statement="handler returns ok",
        subject="handler", kind="function", tags=["x"],
    )
    assert registered["evidence"]["symbol"] == "handler"

    # prepend lines: the range moves, the symbol does not
    target.write_text("# header\n# more\n" + target.read_text())
    assert not check_staleness(
        target, registered["evidence"]["startLine"], registered["evidence"]["endLine"],
        registered["evidence"]["contentHash"],
        symbol=registered["evidence"]["symbol"],
        blob_hash=registered["evidence"]["blobHash"],
    )

    # removing the symbol makes it stale
    target.write_text("def other():\n    return 'ok'\n")
    assert check_staleness(
        target, registered["evidence"]["startLine"], registered["evidence"]["endLine"],
        registered["evidence"]["contentHash"],
        symbol=registered["evidence"]["symbol"],
        blob_hash=registered["evidence"]["blobHash"],
    )


def test_path_activation_includes_relation_hop(fresh_db, tmp_path):
    target = tmp_path / "svc.py"
    target.write_text("def pay():\n    return 1\n")
    direct = register_file_read(
        fresh_db, path=str(target), statement="pay charges the card",
        subject="pay", kind="function", tags=["payments"],
    )
    related = memory_create(
        fresh_db, type="invariant", title="Payments are idempotent",
        statement="Every charge must be idempotent.", tags=["payments"],
        metadata={"verificationMethod": "integration test", "condition": "charge twice"},
    )
    memory_relate(fresh_db, related["id"], direct["id"], "depends_on")

    result = engineering_context(
        fresh_db, tags=[], task="touch payments", paths=[str(target)]
    )
    assert "Payments are idempotent" in result["context"]  # one relation hop


def test_semantic_candidates_hook(fresh_db):
    semantic = memory_create(
        fresh_db, type="gotcha", title="Semantic hit", statement="S", tags=["unrelated"]
    )
    result = engineering_context(
        fresh_db, tags=[], task="x", semantic_candidates=lambda task: [semantic["id"]]
    )
    assert "Semantic hit" in result["context"]
