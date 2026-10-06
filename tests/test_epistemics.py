"""P1 epistemics: provenance, confidence policy, scope, relations, applicability."""

from __future__ import annotations

import pytest

from totem_mcp.context import engineering_context
from totem_mcp.db import get_item, get_relations_for_item
from totem_mcp.tools import memory_create, memory_relate


def test_confidence_derived_from_provenance(fresh_db):
    agent = memory_create(
        fresh_db, type="gotcha", title="Agent claim",
        statement="An agent inferred this claim.", tags=["t"],
    )
    assert get_item(fresh_db, agent["id"]).confidence == 0.6

    user = memory_create(
        fresh_db, type="gotcha", title="User claim",
        statement="The user asserted this claim.", tags=["t"],
        asserted_by="user",
    )
    assert get_item(fresh_db, user["id"]).confidence == 1.0

    tested = memory_create(
        fresh_db, type="gotcha", title="Test claim",
        statement="A test proved this claim.", tags=["t"],
        asserted_by="test",
    )
    assert get_item(fresh_db, tested["id"]).confidence == 0.95

    explicit = memory_create(
        fresh_db, type="gotcha", title="Explicit",
        statement="This claim carries an explicit confidence.", tags=["t"],
        confidence=0.33, asserted_by="user",
    )
    assert get_item(fresh_db, explicit["id"]).confidence == 0.33


def test_hypothesis_confidence_capped(fresh_db):
    result = memory_create(
        fresh_db, type="hypothesis", title="Maybe", statement="S", tags=["t"],
        metadata={"hypothesis": "guess"}, asserted_by="user",
    )
    assert get_item(fresh_db, result["id"]).confidence == 0.4


def test_invalid_provenance_rejected(fresh_db):
    with pytest.raises(ValueError, match="asserted_by"):
        memory_create(
            fresh_db, type="gotcha", title="Bad", statement="S", tags=["t"],
            asserted_by="psychic",
        )


def test_applicability_roundtrip(fresh_db):
    created = memory_create(
        fresh_db, type="decision", title="OAuth", statement="OAuth is supported.",
        tags=["auth"], metadata={"rationale": "legacy"}, applicability="legacy",
    )
    result = engineering_context(fresh_db, tags=["auth"], task="auth")
    assert "Applicability: legacy" in result["context"]


def test_scope_precedence_and_user_context(fresh_db):
    memory_create(
        fresh_db, type="gotcha", title="Project fact",
        statement="This is a project-scoped fact.", tags=["db"],
        scope='{"kind": "project", "value": ""}',
    )
    memory_create(
        fresh_db, type="gotcha", title="Task fact",
        statement="This is a task-scoped fact.", tags=["db"],
        scope='{"kind": "task", "value": "migrate"}',
    )
    memory_create(
        fresh_db, type="gotcha", title="User fact",
        statement="This is a user-scoped fact.", tags=["db"],
        scope='{"kind": "user", "value": "global"}',
    )
    result = engineering_context(fresh_db, tags=["db"], task="migrate")
    text = result["context"]
    assert "USER CONTEXT:" in text and "User fact" in text
    assert text.index("Task fact") < text.index("Project fact")


def test_supersedes_relation_and_status(fresh_db):
    old = memory_create(
        fresh_db, type="decision", title="Use Redis", statement="We use Redis.",
        tags=["infra"], metadata={"rationale": "speed"},
    )
    new = memory_create(
        fresh_db, type="decision", title="Use SQLite", statement="We use SQLite.",
        tags=["infra"], metadata={"rationale": "simpler"}, supersedes_id=old["id"],
    )
    relations = get_relations_for_item(fresh_db, new["id"])
    assert any(r["kind"] == "supersedes" and r["to_id"] == old["id"] for r in relations)
    result = engineering_context(fresh_db, tags=["infra"], task="storage")
    assert "Use Redis" not in result["context"]  # superseded items leave context


def test_contradicts_relation_surfaces_in_context(fresh_db):
    first = memory_create(
        fresh_db, type="constraint", title="No stateful services",
        statement="No external stateful services in production.", tags=["infra"],
        metadata={"constraint": "no stateful services"},
    )
    second = memory_create(
        fresh_db, type="decision", title="Use Redis", statement="We use Redis.",
        tags=["infra"], metadata={"rationale": "queue"},
    )
    memory_relate(fresh_db, first["id"], second["id"], "contradicts")
    result = engineering_context(fresh_db, tags=["infra"], task="infra")
    assert "contradicts" in result["context"]
    assert result["relations"]


def test_invalidates_marks_target(fresh_db):
    old = memory_create(
        fresh_db, type="gotcha", title="Old gotcha",
        statement="The old claim, now obsolete.", tags=["t"],
    )
    new = memory_create(
        fresh_db, type="gotcha", title="New gotcha",
        statement="The new claim that replaces it.", tags=["t"],
    )
    memory_relate(fresh_db, new["id"], old["id"], "invalidates")
    result = engineering_context(fresh_db, tags=["t"], task="t")
    assert "Old gotcha" not in result["context"]


def test_statement_dedup_returns_existing(fresh_db):
    first = memory_create(
        fresh_db, type="gotcha", title="One", statement="Same claim here.", tags=["t"]
    )
    second = memory_create(
        fresh_db, type="gotcha", title="Two", statement="  same   claim HERE. ", tags=["t"]
    )
    assert second.get("duplicate") is True
    assert second["id"] == first["id"]


def test_short_statement_warning(fresh_db):
    result = memory_create(fresh_db, type="gotcha", title="Tiny", statement="x", tags=["t"])
    assert any("very short" in w for w in result.get("warnings", []))


def test_user_db_items_render_as_user_context(fresh_db, tmp_path, monkeypatch):
    """Everything in the user DB is user scope even without an explicit scope."""
    import totem_mcp.context as context_mod
    from totem_mcp.db import connect, init_db

    user_path = tmp_path / "user.db"
    user_conn = connect(db_path=user_path)
    init_db(user_conn)
    memory_create(
        user_conn, type="gotcha", title="Owner habit",
        statement="The owner prefers short answers.", tags=["owner"],
    )
    user_conn.close()

    monkeypatch.setattr(context_mod, "get_user_db_path", lambda: user_path)
    result = engineering_context(fresh_db, tags=[], task="anything")
    assert "USER CONTEXT:" in result["context"]
    assert "Owner habit" in result["context"]


def test_update_revalidates_type_invariants(fresh_db):
    from totem_mcp.tools import memory_update

    item = memory_create(
        fresh_db, type="invariant", title="Inv", statement="S", tags=["x"],
        metadata={"verificationMethod": "test", "condition": "c"},
    )
    # dropping required metadata must be rejected, like create would
    with pytest.raises(Exception):
        memory_update(fresh_db, item["id"], metadata={"note": "oops"})
    stored = get_item(fresh_db, item["id"])
    assert stored.metadata.get("verificationMethod") == "test"


def test_status_lifecycle_rejects_illegal_transition(fresh_db):
    from totem_mcp.models import MemoryStatus
    from totem_mcp.tools import memory_update

    item = memory_create(fresh_db, type="gotcha", title="G", statement="S", tags=["x"])

    # valid: active -> potentially_stale
    memory_update(fresh_db, item["id"], status="potentially_stale")
    assert get_item(fresh_db, item["id"]).status == MemoryStatus.POTENTIALLY_STALE

    # valid: potentially_stale -> invalidated
    memory_update(fresh_db, item["id"], status="invalidated")

    # illegal: invalidated -> active (terminal states may only be deleted)
    with pytest.raises(ValueError):
        memory_update(fresh_db, item["id"], status="active")

    # illegal: unknown status value
    with pytest.raises(ValueError):
        memory_update(fresh_db, item["id"], status="nonsense")


def test_relation_constraints(fresh_db):
    from totem_mcp.db import get_all_relations

    a = memory_create(fresh_db, type="gotcha", title="A", statement="claim A", tags=["t"])
    b = memory_create(fresh_db, type="gotcha", title="B", statement="claim B", tags=["t"])

    # duplicate is idempotent: same relation, no error, no duplicate row
    r1 = memory_relate(fresh_db, a["id"], b["id"], "depends_on")
    r2 = memory_relate(fresh_db, a["id"], b["id"], "depends_on")
    assert r1["id"] == r2["id"]
    assert len([r for r in get_all_relations(fresh_db) if r["kind"] == "depends_on"]) == 1

    # self-relation rejected
    with pytest.raises(ValueError):
        memory_relate(fresh_db, a["id"], a["id"], "depends_on")

    # dangling endpoint rejected
    with pytest.raises(ValueError):
        memory_relate(fresh_db, a["id"], "no-such-id", "depends_on")

    # cycle rejected for state-changing relations
    memory_relate(fresh_db, a["id"], b["id"], "supersedes")
    with pytest.raises(ValueError):
        memory_relate(fresh_db, b["id"], a["id"], "supersedes")


def test_resource_limits_reject_oversized(fresh_db):
    from totem_mcp.models import LIMITS

    with pytest.raises(ValueError):
        memory_create(
            fresh_db, type="gotcha", title="x" * (LIMITS["title"] + 1),
            statement="s", tags=["t"],
        )
    with pytest.raises(ValueError):
        memory_create(
            fresh_db, type="gotcha", title="ok", statement="s",
            tags=[f"t{i}" for i in range(LIMITS["tags"] + 1)],
        )
    with pytest.raises(ValueError):
        memory_create(
            fresh_db, type="gotcha", title="ok", statement="s", tags=["t"],
            metadata={"blob": "x" * (LIMITS["metadata_bytes"] + 1)},
        )


def test_import_size_limit(fresh_db, monkeypatch):
    from totem_mcp.models import LIMITS
    from totem_mcp.tools import memory_import

    monkeypatch.setitem(LIMITS, "import_bytes", 10)
    with pytest.raises(ValueError):
        memory_import(fresh_db, {"items": [], "padding": "0123456789"})
