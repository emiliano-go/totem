from pathlib import Path

from totem_mcp.paths import project_root
from totem_mcp.tools import memory_create, memory_file_context, memory_quality


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_quality_separates_file_state_from_durable_memory(fresh_db):
    root = Path(project_root(fresh_db))
    _write(root, "src/queue.py", '"""Queue helpers."""\n')
    memory_file_context(fresh_db, "src/queue.py")
    memory_create(
        fresh_db,
        type="decision",
        title="Queue retries stop after three attempts",
        statement="The queue retry loop is capped at three attempts.",
        tags=["queue"],
        asserted_by="agent",
    )

    report = memory_quality(fresh_db)
    assert report["total"] == 2
    assert report["fileState"] == 1
    assert report["durable"] == 1
    assert report["signalRatio"] == 0.5
    assert report["digest"][0]["title"] == "Queue retries stop after three attempts"
    assert report["provenance"]["assertedBy"] == 2


def test_quality_counts_duplicate_titles(fresh_db):
    for statement in ("Retries stop after three attempts.", "Retries never exceed three tries."):
        memory_create(
            fresh_db,
            type="gotcha",
            title="Retry cap",
            statement=statement,
            tags=["retry"],
        )
    report = memory_quality(fresh_db)
    assert report["duplicateTitles"] == 1
