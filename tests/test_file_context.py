"""File-state TLDR memory and freshness behavior."""

from pathlib import Path

from totem_mcp.db import get_all_items, get_history, get_item
from totem_mcp.paths import project_root
from totem_mcp.tools import (
    memory_file_context,
    memory_search,
    memory_session_summary,
)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_file_context_creates_and_reuses_tldr(fresh_db):
    root = Path(project_root(fresh_db))
    _write(root, "src/auth.py", '''"""Auth helpers."""

def rotate(token: str) -> str:
    """Rotate a token."""
    return token
''')
    created = memory_file_context(fresh_db, "src/auth.py")
    assert created["created"] is True
    assert created["fresh"] is False
    assert "Auth helpers" in created["tldr"]
    item = get_item(fresh_db, created["id"])
    assert item.metadata["fileKind"] == "file_tldr"
    assert item.evidence[0].path == "src/auth.py"
    assert item.evidence[0].blob_hash == created["blobHash"]

    again = memory_file_context(fresh_db, "src/auth.py")
    assert again["fresh"] is True
    assert again["id"] == created["id"]
    assert len(get_history(fresh_db, created["id"])) == 1


def test_file_context_refreshes_when_content_changes(fresh_db):
    root = Path(project_root(fresh_db))
    _write(root, "README.md", "# Old\n\nOld text.\n")
    first = memory_file_context(fresh_db, "README.md")
    _write(root, "README.md", "# New\n\nNew text with changed content.\n")
    second = memory_file_context(fresh_db, "README.md")
    assert second["id"] == first["id"]
    assert second["changed"] is True
    assert second["fresh"] is False
    item = get_item(fresh_db, second["id"])
    assert "New" in item.statement
    assert item.metadata["blobHash"] == second["blobHash"]
    assert len(get_history(fresh_db, second["id"])) == 2


def test_file_context_can_return_stale_cache_without_writing(fresh_db):
    root = Path(project_root(fresh_db))
    _write(root, "config.json", '{"a": 1}')
    first = memory_file_context(fresh_db, "config.json")
    _write(root, "config.json", '{"a": 2}')
    stale = memory_file_context(fresh_db, "config.json", refresh=False)
    assert stale["fresh"] is False
    assert stale["tldr"] == first["tldr"]
    assert get_item(fresh_db, first["id"]).statement == first["tldr"]


def test_file_state_is_excluded_from_generic_search(fresh_db):
    root = Path(project_root(fresh_db))
    _write(root, "src/queue.py", '"""Queue retry helpers."""\n\ndef retry(n: int) -> int:\n    return n\n')
    created = memory_file_context(fresh_db, "src/queue.py")
    assert memory_search(fresh_db, "queue retry helpers") == []
    hits = memory_search(fresh_db, "queue retry helpers", include_file_state=True)
    assert [hit["id"] for hit in hits] == [created["id"]]


def test_session_summary_updates_one_record(fresh_db):
    first = memory_session_summary(
        fresh_db, "session-1", "Implemented the queue retry behavior.",
        files=["src/queue.py"],
    )
    second = memory_session_summary(
        fresh_db, "session-1", "Documented the queue retry behavior.",
        files=["src/queue.py", "README.md"],
    )
    assert second["id"] == first["id"]
    assert second["action"] == "updated"
    summaries = [
        item for item in get_all_items(fresh_db)
        if (item.metadata or {}).get("kind") == "session_summary"
    ]
    assert len(summaries) == 1
    assert "Documented" in summaries[0].statement


def test_session_summary_links_touched_files(fresh_db):
    root = Path(project_root(fresh_db))
    _write(root, "src/queue.py", '"""Queue helpers."""\n\ndef retry(n: int) -> int:\n    return n\n')
    file_item = memory_file_context(fresh_db, "src/queue.py")
    session = memory_session_summary(
        fresh_db, "session-link", "Changed the queue retry behavior.",
        files=["src/queue.py"],
    )
    from totem_mcp.tools import memory_relations

    relations = memory_relations(fresh_db, session["id"])
    assert any(
        relation["kind"] == "touches" and relation["to_id"] == file_item["id"]
        for relation in relations
    )
