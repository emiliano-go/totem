"""engineering_context tool: context ordering, ambiguity blocking."""

from __future__ import annotations

import turso

from .db import get_all_conflicts, list_items, connect, get_user_db_path
from .hashing import check_staleness
from .models import Conflict, MemoryStatus, MemoryType
from pathlib import Path

# Statuses to skip in context output
_SKIP_STATUSES = {MemoryStatus.DELETED, MemoryStatus.RESOLVED, MemoryStatus.SUPERSEDED}

# Types that get 1.25x score multiplier (spec §11)
_BOOSTED_TYPES = {MemoryType.INVARIANT, MemoryType.CONSTRAINT, MemoryType.AMBIGUITY}


def _score_item(item, tags: list[str], task_words: set[str] | None = None) -> float:
    """Score = 0.30*tagMatch + 0.20*taskSimilarity + 0.15*importance
    + 0.10*confidence + 0.05*recency. Invariants/constraints: 1.25x. Stale: 0.5x."""
    tag_match = len(set(item.tags) & set(tags)) / max(len(tags), 1)
    recency = 1.0

    task_sim = 0.0
    if task_words:
        item_words = set(
            (item.title + " " + item.statement + " " + (item.details or "")).lower().split()
        )
        overlap = len(task_words & item_words)
        if overlap:
            task_sim = min(overlap / max(len(task_words), 1), 1.0)

    score = (
        0.30 * tag_match
        + 0.20 * task_sim
        + 0.25 * item.importance
        + 0.15 * item.confidence
        + 0.10 * recency
    )
    if item.type in _BOOSTED_TYPES:
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
    if item.evidence:
        refs = ", ".join(f"{ev.path}:{ev.start_line}-{ev.end_line}" for ev in item.evidence)
        lines.append(f"  Evidence: {refs}")
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


# Context ordering per spec §13 (types currently implemented)
TYPE_ORDER = [
    MemoryType.CONSTRAINT,
    MemoryType.INVARIANT,
    MemoryType.CONTRACT,
    MemoryType.ARCHITECTURE,
    MemoryType.DECISION,
    MemoryType.AMBIGUITY,
    MemoryType.OBSERVATION,
    MemoryType.GOTCHA,
    MemoryType.BUG,
    MemoryType.HYPOTHESIS,
    MemoryType.IMPLEMENTATION,
    MemoryType.OPEN_QUESTION,
    MemoryType.REJECTED_IDEA,
]

LABELS = {
    MemoryType.CONSTRAINT: "CRITICAL CONSTRAINTS",
    MemoryType.INVARIANT: "CRITICAL INVARIANTS",
    MemoryType.CONTRACT: "RELEVANT CONTRACTS",
    MemoryType.ARCHITECTURE: "ARCHITECTURE",
    MemoryType.DECISION: "DECISIONS",
    MemoryType.AMBIGUITY: "KNOWN AMBIGUITIES",
    MemoryType.OBSERVATION: "OBSERVATIONS",
    MemoryType.GOTCHA: "GOTCHAS",
    MemoryType.BUG: "KNOWN BUGS",
    MemoryType.HYPOTHESIS: "HYPOTHESES",
    MemoryType.IMPLEMENTATION: "CODEBASE FACTS",
    MemoryType.OPEN_QUESTION: "OPEN QUESTIONS",
    MemoryType.REJECTED_IDEA: "REJECTED IDEAS",
}


def engineering_context(
    conn: turso.Connection,
    tags: list[str],
    task: str | None = None,
    token_budget: int | None = None,
    types: list[str] | None = None,
    include_stale: bool = False,
    current_task: str | None = None,
    paths: list[str] | None = None,
) -> dict:
    """Assemble engineering context.

    Pipeline: tag match -> score -> sort -> truncate -> serialize.
    Conflicts, blocking ambiguities, and stale warnings are NEVER dropped for budget.
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
        if item.status in _SKIP_STATUSES:
            continue
        if item.status == MemoryStatus.POTENTIALLY_STALE and not include_stale:
            continue
        # Filter by paths: only include items with evidence matching given paths
        if paths:
            item_paths = {ev.path for ev in item.evidence}
            if not item_paths.intersection(paths):
                continue
        score = _score_item(item, tags, task_words)
        scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Collect blocking ambiguities (type=ambiguity with impact high/critical)
    # Always shown, never budget-truncated
    blocking_ambiguities: list[dict] = []
    non_blocking_ambiguities: list = []
    for _, item in scored:
        if item.type == MemoryType.AMBIGUITY:
            meta = item.metadata or {}
            if meta.get("impact") in ("high", "critical"):
                blocking_ambiguities.append({
                    "id": item.id,
                    "title": item.title,
                    "statement": item.statement,
                    "impact": meta.get("impact"),
                    "blocking": True,
                })
            else:
                non_blocking_ambiguities.append(item)

    # Collect staleness warnings, never budget-truncated
    stale_warnings: list[str] = []
    for _, item in scored:
        for ev in item.evidence:
            if check_staleness(Path(ev.path), ev.start_line, ev.end_line, ev.content_hash):
                stale_warnings.append(
                    f"STALE: [{item.type.value}] {item.title}: {ev.path}:{ev.start_line}-{ev.end_line}"
                )

    # Load stored conflicts, never budget-truncated
    stored_conflicts = get_all_conflicts(conn)

    # Group by type for output ordering
    grouped: dict[str, list] = {t.value: [] for t in TYPE_ORDER}
    for _, item in scored:
        if item.type in grouped:
            grouped[item.type.value].append(item)

    # Build sections
    sections: list[str] = []

    # TASK
    if task:
        sections.append(f"TASK: {task}")

    # BLOCKING AMBIGUITIES: always shown, never budget-truncated
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
        sections.append("\nCONTEXT CONFLICTS:")
        for c in stored_conflicts:
            sections.append(_serialize_conflict(c))
    else:
        sections.append("\nCONTEXT CONFLICTS: (none)")

    # Budget: reserve fixed minimum for meta-sections
    BUDGET_RESERVED_RATIO = 0.3
    effective_budget = token_budget
    if token_budget and token_budget > 0:
        effective_budget = int(token_budget * (1.0 - BUDGET_RESERVED_RATIO))

    # Token estimate: ~4 chars per token for English text
    def _token_estimate(text: str) -> int:
        return max(1, len(text) // 4)

    token_count = 0
    truncated = False

    for type_ in TYPE_ORDER:
        # Ambiguities are split: blocking already shown above, non-blocking go here
        if type_ == MemoryType.AMBIGUITY:
            items_of_type = non_blocking_ambiguities
        else:
            items_of_type = grouped[type_.value]
        if not items_of_type:
            continue
        label = LABELS[type_]
        section_text = f"\n{label}:\n"
        section_tokens = _token_estimate(section_text)
        if effective_budget and token_count + section_tokens > effective_budget:
            truncated = True
            sections.append(f"\n{label}: (truncated, budget exceeded)")
            continue
        sections.append(section_text)
        token_count += section_tokens
        for item in items_of_type:
            item_text = _serialize_item(item)
            item_tokens = _token_estimate(item_text)
            if effective_budget and token_count + item_tokens > effective_budget:
                truncated = True
                sections.append(f"  ... ({len(items_of_type) - items_of_type.index(item)} items truncated)")
                break
            sections.append(item_text)
            token_count += item_tokens

    # STALE WARNINGS: always shown, never budget-truncated
    if stale_warnings:
        sections.append("\nSTALE KNOWLEDGE WARNINGS:")
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
