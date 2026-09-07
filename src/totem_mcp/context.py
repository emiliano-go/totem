"""engineering_context tool: §48 output ordering, §46 ambiguity blocking."""

from __future__ import annotations

import turso

from .db import get_all_conflicts, list_items, connect, get_user_db_path
from .hashing import check_staleness
from .models import Conflict, MemoryStatus, MemoryType
from pathlib import Path


def _score_item(item, tags: list[str], task_words: set[str] | None = None) -> float:
    """Score = 0.4*tagMatch + 0.3*importance + 0.2*confidence + 0.1*recency + taskSimilarity."""
    tag_match = len(set(item.tags) & set(tags)) / max(len(tags), 1)
    recency = 1.0

    # Task similarity: word overlap between task description and item content
    task_sim = 0.0
    if task_words:
        item_words = set(
            (item.title + " " + item.statement + " " + (item.details or "")).lower().split()
        )
        overlap = len(task_words & item_words)
        if overlap:
            task_sim = min(overlap / max(len(task_words), 1), 1.0)

    score = (
        0.3 * tag_match
        + 0.25 * item.importance
        + 0.15 * item.confidence
        + 0.1 * recency
        + 0.2 * task_sim
    )
    if item.type == MemoryType.INVARIANT:
        score *= 1.25
    if item.status == MemoryStatus.POTENTIALLY_STALE:
        score *= 0.5
    return score


def _serialize_item(item) -> str:
    lines = [f"[{item.type.value.upper()}] {item.title}"]
    lines.append(f"  Statement: {item.statement}")
    if item.details:
        lines.append(f"  Details: {item.details}")
    lines.append(f"  Tags: {', '.join(item.tags)}")
    lines.append(f"  Confidence: {item.confidence} | Importance: {item.importance}")
    lines.append(f"  Status: {item.status.value}")
    if item.metadata:
        for k, v in item.metadata.items():
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def _serialize_conflict(c: Conflict) -> str:
    lines = [
        f"  Conflict: {c.item_a} vs {c.item_b}",
        f"    Claim A: {c.claim_a}",
        f"    Claim B: {c.claim_b}",
        f"    Condition: {c.condition}",
        f"    Options: {', '.join(c.resolution_options)}",
    ]
    if c.recommended:
        lines.append(f"    Recommended: {c.recommended}")
    return "\n".join(lines)


def engineering_context(
    conn: turso.Connection,
    tags: list[str],
    task: str | None = None,
    token_budget: int | None = None,
    types: list[str] | None = None,
    include_stale: bool = False,
    current_task: str | None = None,
) -> dict:
    """Assemble engineering context per §48 output ordering.

    Pipeline: tag match -> score -> sort -> truncate -> serialize.
    Conflicts and warnings are NEVER dropped for budget (§48).
    Searches both project DB and user DB (~/.local/share/totem/).
    Project items take precedence on ID collision.
    """
    task_words = set(current_task.lower().split()) if current_task else None
    project_items = list_items(conn, tags=tags, limit=200)

    user_items: list = []
    user_db_path = get_user_db_path()
    if user_db_path.exists():
        user_conn = connect(user_db_path)
        try:
            user_items = list_items(user_conn, tags=tags, limit=200)
        finally:
            user_conn.close()

    seen_ids: set[str] = set()
    all_items: list = []
    for item in project_items:
        if item.id not in seen_ids:
            seen_ids.add(item.id)
            all_items.append(item)
    for item in user_items:
        if item.id not in seen_ids:
            seen_ids.add(item.id)
            all_items.append(item)

    scored = []
    for item in all_items:
        if types and item.type.value not in types:
            continue
        if item.status == MemoryStatus.DELETED:
            continue
        if item.status == MemoryStatus.POTENTIALLY_STALE and not include_stale:
            continue
        score = _score_item(item, tags, task_words)
        scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)

    # §46: Collect blocking ambiguities, always shown, never budget-truncated
    blocking_ambiguities: list[dict] = []
    for _, item in scored:
        meta = item.metadata or {}
        if meta.get("flagged_ambiguity") and meta.get("impact") in ("high", "critical"):
            blocking_ambiguities.append({
                "id": item.id,
                "title": item.title,
                "statement": item.statement,
                "impact": meta.get("impact"),
                "blocking": True,
            })

    # Collect staleness warnings, never budget-truncated
    stale_warnings: list[str] = []
    for _, item in scored:
        for ev in item.evidence:
            if check_staleness(Path(ev.path), ev.start_line, ev.end_line, ev.content_hash):
                stale_warnings.append(
                    f"STALE: [{item.type.value}] {item.title}: {ev.path}:{ev.start_line}-{ev.end_line}"
                )

    # §42: Load stored conflicts, never budget-truncated
    stored_conflicts = get_all_conflicts(conn)

    # Group by type for §48 ordering
    TYPE_ORDER = [
        MemoryType.INVARIANT,
        MemoryType.DECISION,
        MemoryType.GOTCHA,
        MemoryType.REJECTED_IDEA,
    ]
    grouped: dict[str, list] = {t.value: [] for t in TYPE_ORDER}
    for _, item in scored:
        grouped[item.type.value].append(item)

    # Build sections per §48 output ordering
    sections: list[str] = []

    # TASK
    if task:
        sections.append(f"TASK: {task}")

    # BLOCKING AMBIGUITIES (§46): always shown, never budget-truncated
    if blocking_ambiguities:
        sections.append("\nBLOCKING AMBIGUITIES:")
        for amb in blocking_ambiguities:
            sections.append(f"  [BLOCKING] {amb['title']}")
            sections.append(f"    Statement: {amb['statement']}")
            sections.append(f"    Impact: {amb['impact']}")
    else:
        sections.append("\nBLOCKING AMBIGUITIES: (none)")

    # CONFLICTS: always shown, never budget-truncated
    if stored_conflicts:
        sections.append("\nCONFLICTS:")
        for c in stored_conflicts:
            sections.append(_serialize_conflict(c))
    else:
        sections.append("\nCONFLICTS: (none)")

    # §48: CRITICAL INVARIANTS, DECISIONS, GOTCHAS, REJECTED IDEAS
    LABELS = {
        MemoryType.INVARIANT: "CRITICAL INVARIANTS",
        MemoryType.DECISION: "DECISIONS",
        MemoryType.GOTCHA: "GOTCHAS",
        MemoryType.REJECTED_IDEA: "REJECTED IDEAS",
    }

    # Budget: reserve fixed minimum for non-item sections
    # (ambiguities + conflicts + warnings are never truncated)
    BUDGET_RESERVED_RATIO = 0.3  # 30% reserved for meta-sections
    effective_budget = token_budget
    if token_budget and token_budget > 0:
        effective_budget = int(token_budget * (1.0 - BUDGET_RESERVED_RATIO))

    char_count = 0
    truncated = False

    for type_ in TYPE_ORDER:
        items_of_type = grouped[type_.value]
        if not items_of_type:
            continue
        label = LABELS[type_]
        section_text = f"\n{label}:\n"
        if effective_budget and char_count + len(section_text) > effective_budget:
            truncated = True
            sections.append(f"\n{label}: (truncated, budget exceeded)")
            continue
        sections.append(section_text)
        char_count += len(section_text)
        for item in items_of_type:
            item_text = _serialize_item(item)
            if effective_budget and char_count + len(item_text) > effective_budget:
                truncated = True
                sections.append(f"  ... ({len(items_of_type) - items_of_type.index(item)} items truncated)")
                break
            sections.append(item_text)
            char_count += len(item_text)

    # STALE WARNINGS: always shown, never budget-truncated
    if stale_warnings:
        sections.append("\nSTALE WARNINGS:")
        for w in stale_warnings:
            sections.append(f"  {w}")

    context = "\n".join(sections)

    selected_ids = [item.id for _, item in scored]
    omitted_ids = []
    if truncated:
        omitted_ids = [item.id for _, item in scored if item.id not in selected_ids]

    return {
        "context": context,
        "selectedIds": selected_ids,
        "omittedIds": omitted_ids,
        "conflicts": [c.model_dump(by_alias=True) for c in stored_conflicts],
        "warnings": stale_warnings,
    }
