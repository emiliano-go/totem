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


def _check(ev, target):
    return check_staleness(
        target,
        ev["startLine"],
        ev["endLine"],
        ev["contentHash"],
        symbol=ev.get("symbol"),
        blob_hash=ev.get("blobHash"),
        symbol_hash=ev.get("symbolHash"),
    )


def test_symbol_body_change_is_stale(fresh_db, tmp_path):
    target = tmp_path / "svc.py"
    target.write_text("def validate_token():\n    return jwt.verify(token)\n")
    reg = register_file_read(
        fresh_db, path=str(target), statement="validate_token verifies the JWT",
        subject="validate_token", kind="function", tags=["auth"],
    )
    ev = reg["evidence"]
    assert ev["symbol"] == "validate_token"
    assert ev["symbolHash"]

    # moved + reformatted, same body -> fresh
    target.write_text("# header\n\n" + "def validate_token():\n        return jwt.verify(token)\n")
    assert not _check(ev, target)

    # materially changed body -> stale (the P0 case)
    target.write_text("def validate_token():\n    return True\n")
    assert _check(ev, target)

    # unrelated file edit, symbol unchanged -> fresh
    target.write_text(
        "def validate_token():\n    return jwt.verify(token)\n\n\ndef other():\n    return 2\n"
    )
    assert not _check(ev, target)


def test_legacy_symbol_evidence_without_hash_is_presence_only(fresh_db, tmp_path):
    target = tmp_path / "m.py"
    body = "def f():\n    return 1\n"
    target.write_text(body)
    # no symbolHash -> legacy presence-only behaviour
    assert not check_staleness(
        target, 1, 2, hash_content(body), symbol="f", blob_hash=None, symbol_hash=None
    )
    target.write_text("def f():\n    return 999\n")
    assert not check_staleness(
        target, 1, 2, hash_content(body), symbol="f", blob_hash=None, symbol_hash=None
    )


def test_locator_and_statement_index(fresh_db, tmp_path):
    from totem_mcp.db import find_by_statement_normalized, find_locator_item

    target = tmp_path / "idx.py"
    target.write_text("def f():\n    return 1\n")
    reg = register_file_read(
        fresh_db, path=str(target), statement="f returns 1",
        subject="f", kind="function", tags=["t"],
    )
    # (path, subject) resolves through the locator index, not a corpus scan
    assert find_locator_item(fresh_db, str(target), "f") == reg["id"]
    # density dedup resolves through the normalized-statement index
    hit = find_by_statement_normalized(fresh_db, "  F   Returns   1 ")
    assert hit is not None and hit.id == reg["id"]
