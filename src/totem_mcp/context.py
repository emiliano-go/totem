"""engineering_context tool: context ordering, ambiguity blocking."""

from __future__ import annotations

import fnmatch
import json

import turso

from .db import (
    get_item,
    get_open_conflicts,
    get_relations_for_items,
    init_db,
    list_items,
    connect,
    get_user_db_path,
)
from .hashing import check_staleness
from .models import Conflict, MemoryStatus, MemoryType, scope_kind
from pathlib import Path

# Statuses to skip in context output
_SKIP_STATUSES = {
    MemoryStatus.DELETED,
    MemoryStatus.RESOLVED,
    MemoryStatus.SUPERSEDED,
    MemoryStatus.INVALIDATED,
}

# Types that get 1.25x score multiplier (spec §11)
_BOOSTED_TYPES = {MemoryType.INVARIANT, MemoryType.CONSTRAINT, MemoryType.AMBIGUITY}


def _scope_value(scope: str | None) -> str:
    if not scope:
        return ""
    text = scope.strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return str(data.get("value") or "")
        except ValueError:
            pass
    return text


def _scope_boost(item, paths: list[str] | None) -> float:
    """Scope precedence: task/path-specific knowledge outranks project/global."""
    kind = scope_kind(item.scope)
    if kind == "task":
        return 1.15
    if kind == "user":
        return 1.05
    if kind == "path" and paths:
        value = _scope_value(item.scope)
        if value and any(fnmatch.fnmatch(p, value) or value in p for p in paths):
            return 1.15
    return 1.0


def _score_item(
    item,
    tags: list[str],
    task_words: set[str] | None = None,
    paths: list[str] | None = None,
) -> float:
    """Score = 0.30*tagMatch + 0.20*taskSimilarity + 0.25*importance
    + 0.15*confidence + 0.10*recency. Invariants/constraints/ambiguities: 1.25x.
    Scope precedence applies; stale: 0.5x."""
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
    score *= _scope_boost(item, paths)
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
    if item.scope:
        kind = scope_kind(item.scope)
        value = _scope_value(item.scope)
        lines.append(f"  Scope: {kind} ({value})" if value and value != kind else f"  Scope: {kind}")
    if item.asserted_by:
        lines.append(f"  Asserted by: {item.asserted_by}")
    if item.applicability:
        lines.append(f"  Applicability: {item.applicability}")
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
    semantic_candidates=None,
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
        user_conn = None
        try:
            user_conn = connect(user_db_path)
            init_db(user_conn)  # idempotent; migrates old-schema user DBs
            user_items = list_items(user_conn, tags=tags, limit=200)
        except Exception:
            # A broken user DB must not take down project results
            user_items = []
        finally:
            if user_conn is not None:
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

    # Path activation: direct evidence matches plus one bounded relation hop;
    # semantic_candidates is an optional extra candidate source (embeddings).
    extra_ids: set[str] = set()
    path_matched: set[str] = set()
    if paths:
        path_matched = {
            item.id
            for item in all_items
            if {ev.path for ev in item.evidence} & set(paths)
        }
        for rel in get_relations_for_items(conn, list(path_matched)):
            extra_ids.update({rel["from_id"], rel["to_id"]} - path_matched)
    if semantic_candidates is not None:
        try:
            extra_ids.update(semantic_candidates(task or "") or [])
        except Exception:
            pass
    if extra_ids:
        known = {item.id for item in all_items}
        for item_id in extra_ids:
            if item_id in known:
                continue
            item = get_item(conn, item_id)
            if item is not None:
                all_items.append(item)
                known.add(item.id)

    scored = []
    stale_items: list = []
    for item in all_items:
        if types and item.type.value not in types:
            continue
        if item.status in _SKIP_STATUSES:
            continue
        # Filter by paths, but keep relation-hop and semantic candidates
        if paths and item.id not in path_matched and item.id not in extra_ids:
            continue
        if item.status == MemoryStatus.POTENTIALLY_STALE:
            # Stale knowledge stays visible (in its own section) instead of
            # silently disappearing from context.
            stale_items.append(item)
            if not include_stale:
                continue
        score = _score_item(item, tags, task_words, paths)
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

    # Collect staleness warnings over everything we considered, including items
    # already flagged stale (they must not vanish without a trace).
    stale_warnings: list[str] = []
    seen_warnings: set[tuple] = set()
    for item in [i for _, i in scored] + stale_items:
        for ev in item.evidence:
            key = (item.id, ev.path, ev.start_line, ev.end_line)
            if key in seen_warnings:
                continue
            if check_staleness(
                Path(ev.path),
                ev.start_line,
                ev.end_line,
                ev.content_hash,
                symbol=ev.symbol,
                blob_hash=ev.blob_hash,
            ):
                seen_warnings.add(key)
                stale_warnings.append(
                    f"STALE: [{item.type.value}] {item.title}: {ev.path}:{ev.start_line}-{ev.end_line}"
                )

    # Load unresolved conflicts, never budget-truncated
    stored_conflicts = get_open_conflicts(conn)

    # Group by type for output ordering (user-scope items live in USER CONTEXT)
    grouped: dict[str, list] = {t.value: [] for t in TYPE_ORDER}
    for _, item in scored:
        if scope_kind(item.scope) == "user":
            continue
        if item.type in grouped:
            grouped[item.type.value].append(item)

    # Build sections
    sections: list[str] = []

    # TASK
    if task:
        sections.append(f"TASK: {task}")

    # USER CONTEXT: globally applicable owner memory, always shown first
    user_items = [item for _, item in scored if scope_kind(item.scope) == "user"]
    sections.append("\nUSER CONTEXT:")
    if user_items:
        for item in user_items:
            sections.append(_serialize_item(item))
    else:
        sections.append("  (none)")

    sections.append("\nPROJECT CONTEXT:")

    # BLOCKING AMBIGUITIES: always shown, never budget-truncated
    if blocking_ambiguities:
        sections.append("\nBLOCKING AMBIGUITIES:")
        for amb in blocking_ambiguities:
            sections.append(f"  [BLOCKING] {amb['title']}")
            sections.append(f"    Statement: {amb['statement']}")
            sections.append(f"    Impact: {amb['impact']}")
    else:
        sections.append("\nBLOCKING AMBIGUITIES: (none)")

    # CONFLICTS: stored + explicit contradicts relations, never budget-truncated
    relation_conflicts: list[str] = []
    by_id = {item.id: item for _, item in scored}
    selected_all = [item.id for _, item in scored] + [i.id for i in stale_items]
    relations = get_relations_for_items(conn, selected_all)
    for rel in relations:
        if rel["kind"] != "contradicts":
            continue
        a = by_id.get(rel["from_id"])
        b = by_id.get(rel["to_id"])
        relation_conflicts.append(
            f"  [relation] {(a.title if a else rel['from_id'])} contradicts "
            f"{(b.title if b else rel['to_id'])}"
        )
    if stored_conflicts or relation_conflicts:
        sections.append("\nCONTEXT CONFLICTS:")
        for c in stored_conflicts:
            sections.append(_serialize_conflict(c))
        for line in relation_conflicts:
            sections.append(line)
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
    included_ids: set[str] = {a["id"] for a in blocking_ambiguities} | {
        item.id for item in user_items
    }
    omitted_ids: list[str] = []

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
            omitted_ids.extend(item.id for item in items_of_type)
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
                omitted_ids.extend(it.id for it in items_of_type[items_of_type.index(item):])
                break
            sections.append(item_text)
            token_count += item_tokens
            included_ids.add(item.id)

    # STALE WARNINGS: always shown, never budget-truncated
    if stale_warnings:
        sections.append("\nSTALE KNOWLEDGE WARNINGS:")
        for w in stale_warnings:
            sections.append(f"  {w}")

    # STALE KNOWLEDGE: previously-trusted items whose evidence changed. High-risk
    # classes (constraints/invariants/contracts/ambiguities) are always shown;
    # the rest are budget-truncated last.
    stale_ids: list[str] = []
    if stale_items:
        always = {
            MemoryType.CONSTRAINT,
            MemoryType.INVARIANT,
            MemoryType.CONTRACT,
            MemoryType.AMBIGUITY,
        }
        sections.append("\nSTALE KNOWLEDGE (verify before relying):")
        omitted_stale = 0
        for item in stale_items:
            item_text = _serialize_item(item)
            item_tokens = _token_estimate(item_text)
            if (
                item.type in always
                or not effective_budget
                or token_count + item_tokens <= effective_budget
            ):
                sections.append(item_text)
                token_count += item_tokens
                stale_ids.append(item.id)
            else:
                omitted_stale += 1
        if omitted_stale:
            sections.append(f"  ... ({omitted_stale} lower-priority stale items omitted)")

    context = "\n".join(sections)

    selected_ids = [item.id for _, item in scored if item.id in included_ids]

    return {
        "context": context,
        "selectedIds": selected_ids,
        "omittedIds": omitted_ids,
        "staleIds": stale_ids,
        "conflicts": [c.model_dump(by_alias=True) for c in stored_conflicts],
        "relations": relations,
        "warnings": stale_warnings,
    }
