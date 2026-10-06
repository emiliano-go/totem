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
        fresh_db, type="gotcha", title="Old gotcha", statement="S", tags=["t"]
    )
    new = memory_create(
        fresh_db, type="gotcha", title="New gotcha", statement="S", tags=["t"]
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
