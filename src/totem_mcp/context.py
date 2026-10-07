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


def _task_scope_matches(item, task_words: set[str] | None) -> bool:
    """True when a task-scoped memory's value actually matches the current task."""
    if scope_kind(item.scope) != "task" or not task_words:
        return False
    value = _scope_value(item.scope).lower()
    if not value:
        return False
    return value in task_words or any(word and word in value for word in task_words)


def _scope_boost(item, paths: list[str] | None, task_words: set[str] | None = None) -> float:
    """Scope precedence: task/path-specific knowledge outranks project/global.

    A task scope only boosts when its value matches the current task, so
    ``scope=task:migrate`` does not boost an unrelated CSS task.
    """
    kind = scope_kind(item.scope)
    if kind == "task":
        return 1.15 if _task_scope_matches(item, task_words) else 1.0
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
    score *= _scope_boost(item, paths, task_words)
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

# Hard per-section caps so "emergency" sections cannot blow the token budget.
MAX_CONFLICTS = 20
MAX_AMBIGUITIES = 20
MAX_STALE_WARNINGS = 30
MAX_USER_ITEMS = 30
MAX_STALE_ITEMS = 30
MAX_TOKEN_BUDGET = 50_000


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
    if token_budget and token_budget > MAX_TOKEN_BUDGET:
        token_budget = MAX_TOKEN_BUDGET
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
    db_user_ids: set[str] = set()
    for item in user_items:
        if item.id not in seen_ids:
            seen_ids.add(item.id)
            all_items.append(item)
            db_user_ids.add(item.id)

    # Path activation: direct evidence matches plus one bounded relation hop;
    # semantic_candidates is an optional extra candidate source (embeddings).
    extra_ids: set[str] = set()
    relation_hop_ids: set[str] = set()
    semantic_ids: set[str] = set()
    path_matched: set[str] = set()
    if paths:
        path_matched = {
            item.id
            for item in all_items
            if {ev.path for ev in item.evidence} & set(paths)
        }
        for rel in get_relations_for_items(conn, list(path_matched)):
            relation_hop_ids.update({rel["from_id"], rel["to_id"]} - path_matched)
        extra_ids.update(relation_hop_ids)
    if semantic_candidates is not None:
        try:
            semantic_ids = set(semantic_candidates(task or "") or [])
        except Exception:
            semantic_ids = set()
        extra_ids.update(semantic_ids)
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
                symbol_hash=ev.symbol_hash,
            ):
                seen_warnings.add(key)
                stale_warnings.append(
                    f"STALE: [{item.type.value}] {item.title}: {ev.path}:{ev.start_line}-{ev.end_line}"
                )

    # All unresolved conflicts (relevance-filtered below, once the candidate set
    # is known).
    open_conflicts = get_open_conflicts(conn)

    # Group by type for output ordering (user-scope items live in USER CONTEXT)
    grouped: dict[str, list] = {t.value: [] for t in TYPE_ORDER}
    user_ids = {item.id for item in user_items}
    for _, item in scored:
        if item.id in user_ids:
            continue
        if item.type in grouped:
            grouped[item.type.value].append(item)

    # Build ordered blocks so the whole result can be hard-bounded. Essential
    # blocks are always emitted (subject to the final hard trim); the rest are
    # included only while they fit.
    def _tok(text: str) -> int:
        return max(1, len(text) // 4)

    omitted_ids: list[str] = []
    omitted = {
        "conflicts": 0,
        "ambiguities": 0,
        "staleWarnings": 0,
        "stale": 0,
        "user": 0,
        "items": 0,
    }
    blocks: list[dict] = []

    def add_block(essential: bool, lines: list[str], ids: list[str]) -> None:
        blocks.append({"essential": essential, "lines": lines, "ids": ids})

    # TASK
    if task:
        add_block(True, [f"TASK: {task}"], [])

    # USER CONTEXT (bounded)
    user_lines = ["\nUSER CONTEXT:"]
    kept_user = user_items[:MAX_USER_ITEMS]
    omitted["user"] = max(0, len(user_items) - len(kept_user))
    for item in kept_user:
        user_lines.append(_serialize_item(item))
    if omitted["user"]:
        user_lines.append(f"  ... ({omitted['user']} more user items omitted)")
    if not kept_user:
        user_lines.append("  (none)")
    add_block(True, user_lines, [i.id for i in kept_user])

    # BLOCKING AMBIGUITIES (bounded)
    amb_lines = ["\nBLOCKING AMBIGUITIES:"]
    kept_amb = blocking_ambiguities[:MAX_AMBIGUITIES]
    omitted["ambiguities"] = max(0, len(blocking_ambiguities) - len(kept_amb))
    for amb in kept_amb:
        amb_lines.append(f"  [BLOCKING] {amb['title']}")
        amb_lines.append(f"    Statement: {amb['statement']}")
        amb_lines.append(f"    Impact: {amb['impact']}")
    if omitted["ambiguities"]:
        amb_lines.append(f"  ... ({omitted['ambiguities']} more blocking ambiguities omitted)")
    if not kept_amb:
        amb_lines.append("  (none)")
    add_block(True, amb_lines, [a["id"] for a in kept_amb])

    # CONFLICTS: stored + explicit contradicts relations (bounded)
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
    conflict_lines = ["\nCONTEXT CONFLICTS:"]
    # Relevance: keep a conflict only when it touches a memory we actually
    # considered, or one of the critical classes (invariants/constraints/
    # contracts) that are globally relevant. Everything else is context noise.
    relevant_ids = set(by_id) | {i.id for i in stale_items} | path_matched | extra_ids
    critical_types = {MemoryType.INVARIANT, MemoryType.CONSTRAINT, MemoryType.CONTRACT}

    def _conflict_relevant(conflict) -> bool:
        if conflict.item_a in relevant_ids or conflict.item_b in relevant_ids:
            return True
        for endpoint in (conflict.item_a, conflict.item_b):
            item = by_id.get(endpoint) or get_item(conn, endpoint)
            if item is not None and item.type in critical_types:
                return True
        return False

    stored_conflicts = [c for c in open_conflicts if _conflict_relevant(c)]
    kept_conf = stored_conflicts[:MAX_CONFLICTS]
    omitted["conflicts"] = max(0, len(stored_conflicts) - len(kept_conf))
    for c in kept_conf:
        conflict_lines.append(_serialize_conflict(c))
    remaining_conf = max(0, MAX_CONFLICTS - len(kept_conf))
    conflict_lines.extend(relation_conflicts[:remaining_conf])
    if omitted["conflicts"]:
        conflict_lines.append(f"  ... ({omitted['conflicts']} more conflicts omitted)")
    if not kept_conf and not relation_conflicts:
        conflict_lines.append("  (none)")
    add_block(True, conflict_lines, [])

    # Type sections (non-essential, budget-limited)
    for type_ in TYPE_ORDER:
        if type_ == MemoryType.AMBIGUITY:
            items_of_type = non_blocking_ambiguities
        else:
            items_of_type = grouped[type_.value]
        if not items_of_type:
            continue
        lines = [f"\n{LABELS[type_]}:"] + [_serialize_item(item) for item in items_of_type]
        add_block(False, lines, [item.id for item in items_of_type])

    # STALE WARNINGS (bounded)
    if stale_warnings:
        w_lines = ["\nSTALE KNOWLEDGE WARNINGS:"]
        kept_w = stale_warnings[:MAX_STALE_WARNINGS]
        omitted["staleWarnings"] = len(stale_warnings) - len(kept_w)
        w_lines.extend(f"  {w}" for w in kept_w)
        if omitted["staleWarnings"]:
            w_lines.append(f"  ... ({omitted['staleWarnings']} more warnings omitted)")
        add_block(False, w_lines, [])

    # STALE KNOWLEDGE (bounded; high-risk classes first)
    always = {
        MemoryType.CONSTRAINT,
        MemoryType.INVARIANT,
        MemoryType.CONTRACT,
        MemoryType.AMBIGUITY,
    }
    kept_stale: list = []
    if stale_items:
        ordered = sorted(stale_items, key=lambda i: i.type not in always)
        kept_stale = ordered[:MAX_STALE_ITEMS]
        s_lines = ["\nSTALE KNOWLEDGE (verify before relying):"]
        s_lines.extend(_serialize_item(item) for item in kept_stale)
        omitted["stale"] = max(0, len(stale_items) - len(kept_stale))
        if omitted["stale"]:
            s_lines.append(f"  ... ({omitted['stale']} lower-priority stale items omitted)")
        add_block(False, s_lines, [i.id for i in kept_stale])

    # Assemble in order; token_budget is a HARD upper bound on the result.
    budget = token_budget if (token_budget and token_budget > 0) else None
    included_blocks: list[dict] = []
    token_count = 0
    for block in blocks:
        block_tokens = sum(_tok(line) for line in block["lines"])
        if block["essential"] or budget is None or token_count + block_tokens <= budget:
            included_blocks.append(block)
            token_count += block_tokens
        else:
            omitted["items"] += len(block["ids"])
            omitted_ids.extend(block["ids"])

    # If even the essential blocks exceed the budget, trim from the end (lowest
    # priority) until within budget.
    if budget is not None:
        while included_blocks and token_count > budget:
            dropped = included_blocks.pop()
            token_count -= sum(_tok(line) for line in dropped["lines"])
            omitted["items"] += len(dropped["ids"])
            omitted_ids.extend(dropped["ids"])

    sections: list[str] = []
    included_ids: set[str] = set()
    for block in included_blocks:
        sections.extend(block["lines"])
        included_ids.update(block["ids"])
    context = "\n".join(sections)

    selected_ids = [item.id for _, item in scored if item.id in included_ids]

    # Explainability: why each included memory entered context.
    item_map = dict(by_id)
    for stale in stale_items:
        item_map.setdefault(stale.id, stale)
    tag_set = set(tags)
    why: dict[str, list[str]] = {}
    for item_id in included_ids:
        item = item_map.get(item_id)
        reasons: list[str] = []
        if item_id in db_user_ids or (item and scope_kind(item.scope) == "user"):
            reasons.append("user-scope")
        if item_id in path_matched:
            reasons.append("path")
        if item_id in relation_hop_ids:
            reasons.append("relation-hop")
        if item_id in semantic_ids:
            reasons.append("semantic")
        if item and tag_set and (set(item.tags) & tag_set):
            reasons.append("tag")
        if item and _task_scope_matches(item, task_words):
            reasons.append("task-scope")
        if item and item.type in _BOOSTED_TYPES:
            reasons.append("critical")
        why[item_id] = reasons

    return {
        "context": context,
        "selectedIds": selected_ids,
        "omittedIds": omitted_ids,
        "staleIds": [i.id for i in kept_stale if i.id in included_ids],
        "conflicts": [c.model_dump(by_alias=True) for c in stored_conflicts],
        "relations": relations,
        "warnings": stale_warnings,
        "budget": token_budget,
        "estimatedTokens": token_count,
        "omitted": omitted,
        "why": why,
    }
