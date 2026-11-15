"""Corpus-wide candidate generation for memory retrieval.

The context compiler used to start from tag-filtered items and re-rank only
those.  That made recall a function of tagging discipline.  This module builds
candidates from the whole database instead:

* lexical FTS hits (the full query and each term),
* dense similarity over persisted embeddings,
* evidence path and symbol matches,
* tag matches (a boost, not a gate),
* a two-hop relation expansion from the strongest seeds.

Candidates are fused and returned with the reason each one entered, so the
context compiler can explain a selection and still apply its deterministic
type, scope, and trust adjustments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import turso

from .db import (
    get_all_items,
    get_embeddings,
    get_relations_for_items,
    search_fts,
    upsert_embedding,
)
from .embeddings import configured_model, get_static_encoder
from .hashing import hash_content
from .ranking import DenseEncoder, cosine, rrf_fuse

MAX_QUERY_TERMS = 8
MAX_CANDIDATES = 400
GRAPH_SEEDS = 25
GRAPH_HOPS = 2
GRAPH_DECAY = 0.5


def content_text(item) -> str:
    """Text used for lexical and dense representations of one memory."""
    meta = getattr(item, "metadata", None) or {}
    extra = []
    for key in ("searchTerms", "aliases", "symbol", "subject", "component"):
        value = meta.get(key)
        if isinstance(value, str):
            extra.append(value)
        elif isinstance(value, (list, tuple)):
            extra.extend(str(v) for v in value)
    parts = [
        item.title, item.statement, item.details or "",
        " ".join(item.tags), " ".join(extra),
    ]
    return "\n".join(part for part in parts if part)


def content_hash(item) -> str:
    return hash_content(content_text(item))


def _query_terms(query: str) -> list[str]:
    terms = []
    for raw in (query or "").split():
        term = raw.strip(".,;:()[]{}\"'").lower()
        if len(term) >= 2 and term not in terms:
            terms.append(term)
    return terms[:MAX_QUERY_TERMS]


@dataclass
class EncoderBundle:
    vectors: dict[str, list[float]]
    backend: str
    model: str
    dim: int
    static: object | None = None
    dense: DenseEncoder | None = None

    def query_vector(self, query: str) -> list[float]:
        if self.static is not None:
            return self.static.encode(query)
        assert self.dense is not None
        return self.dense.encode(query)


def get_encoder_bundle(conn: turso.Connection, items) -> EncoderBundle:
    """Embeddings for every item, reusing persisted static vectors."""
    texts = [content_text(item) for item in items]
    static = get_static_encoder()
    if static is not None:
        model = f"model2vec:{configured_model()}"
        stored = get_embeddings(conn, model)
        hashes = {item.id: content_hash(item) for item in items}
        missing = [
            (index, item) for index, item in enumerate(items)
            if item.id not in stored or stored[item.id]["contentHash"] != hashes[item.id]
        ]
        for start in range(0, len(missing), 64):
            chunk = missing[start:start + 64]
            vectors = static.encode_many([texts[index] for index, _ in chunk])
            for (index, item), vector in zip(chunk, vectors):
                upsert_embedding(
                    conn, item.id, model, static.dim, vector, hashes[item.id]
                )
        vectors = {}
        for item in items:
            row = stored.get(item.id)
            if row is not None and row["contentHash"] == hashes[item.id]:
                vectors[item.id] = row["vector"]
        # Rows written in this call were not in ``stored``; read them back once.
        missing_ids = [item.id for item in items if item.id not in vectors]
        if missing_ids:
            fresh = get_embeddings(conn, model)
            for item_id in missing_ids:
                row = fresh.get(item_id)
                if row is not None:
                    vectors[item_id] = row["vector"]
        return EncoderBundle(
            vectors=vectors, backend="model2vec", model=model,
            dim=static.dim, static=static,
        )

    dense = DenseEncoder().fit(texts + [" "])
    vectors = {item.id: dense.encode(text) for item, text in zip(items, texts)}
    return EncoderBundle(
        vectors=vectors, backend="hash", model=f"hash:{dense.dim}",
        dim=dense.dim, dense=dense,
    )


@dataclass
class Candidate:
    item: object
    lexical: float = 0.0
    dense: float = 0.0
    path: float = 0.0
    tag: float = 0.0
    graph: float = 0.0
    validity: float = 1.0
    state: str = "current"
    reasons: list[str] = field(default_factory=list)
    fused: float = 0.0

    def add_reason(self, reason: str) -> None:
        if reason not in self.reasons:
            self.reasons.append(reason)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def temporal_state(item, as_of: datetime | None = None) -> str:
    """Return 'current', 'future', or 'expired' at ``as_of`` (default now)."""
    moment = as_of or datetime.now(timezone.utc)
    start = _parse_time(getattr(item, "valid_from", None))
    end = _parse_time(getattr(item, "valid_to", None))
    if start is not None and moment < start:
        return "future"
    if end is not None and moment > end:
        return "expired"
    return "current"


def validity(item, as_of: datetime | None = None) -> float:
    """1.0 while the claim is valid at ``as_of`` (default now), else 0.0."""
    return 1.0 if temporal_state(item, as_of) == "current" else 0.0


def _path_hits(item, paths: set[str]) -> tuple[float, bool]:
    if not paths:
        return 0.0, False
    score = 0.0
    for evidence in getattr(item, "evidence", None) or []:
        path = evidence.path.replace("\\", "/")
        for wanted in paths:
            normalized = wanted.replace("\\", "/")
            if path == normalized or path.endswith("/" + normalized) or normalized.endswith("/" + path):
                score = 1.0
                break
        if score:
            break
    return score, score > 0


def _symbol_hit(item, terms: set[str]) -> bool:
    meta = getattr(item, "metadata", None) or {}
    symbol = str(meta.get("symbol") or meta.get("subject") or "").lower()
    if not symbol:
        return False
    return symbol in terms or any(part and part in symbol for part in terms)


def _graph_scores(seed_ids: list[str], relations: list[dict]) -> dict[str, float]:
    if not seed_ids:
        return {}
    adjacency: dict[str, set[str]] = {}
    for relation in relations:
        left, right = relation["from_id"], relation["to_id"]
        adjacency.setdefault(left, set()).add(right)
        adjacency.setdefault(right, set()).add(left)
    scores: dict[str, float] = {}
    frontier = {item_id: 1.0 for item_id in seed_ids}
    for _hop in range(GRAPH_HOPS):
        next_frontier: dict[str, float] = {}
        for item_id, weight in frontier.items():
            for neighbor in adjacency.get(item_id, ()):
                candidate = weight * GRAPH_DECAY
                if candidate > scores.get(neighbor, 0.0):
                    scores[neighbor] = candidate
                    next_frontier[neighbor] = max(
                        next_frontier.get(neighbor, 0.0), candidate
                    )
        frontier = next_frontier
        if not frontier:
            break
    return scores


def build_candidates(
    conn: turso.Connection,
    query: str = "",
    tags: list[str] | None = None,
    paths: list[str] | None = None,
    *,
    limit: int = MAX_CANDIDATES,
    include_stale: bool = True,
    include_expired: bool = False,
    as_of: datetime | None = None,
    semantic: bool = True,
) -> tuple[list[Candidate], dict]:
    """Build fused candidates over the whole memory corpus."""
    items = get_all_items(conn)
    if not items:
        return [], {"backend": "off", "model": "", "dim": 0, "total": 0}

    if semantic:
        bundle = get_encoder_bundle(conn, items)
    else:
        bundle = EncoderBundle(vectors={}, backend="off", model="", dim=0)
    by_id = {item.id: item for item in items}
    candidates: dict[str, Candidate] = {
        item.id: Candidate(item=item) for item in items
    }
    for item in items:
        state = temporal_state(item, as_of)
        candidates[item.id].state = state
        candidates[item.id].validity = 1.0 if state == "current" else 0.0

    terms = _query_terms(query)
    tag_set = {tag.lower() for tag in (tags or [])}
    path_set = set(paths or [])
    query_set = set(terms)

    # Lexical leg: the full query plus each term, so a multiword query is not
    # silently ANDed into zero results.
    lexical_rank: dict[str, float] = {}
    queries = [query] if query.strip() else []
    queries.extend(terms)
    for text in dict.fromkeys(queries):
        try:
            hits = search_fts(conn, text, include_stale=True, limit=limit)
        except Exception:
            hits = []
        for rank, item in enumerate(hits, start=1):
            lexical_rank[item.id] = max(
                lexical_rank.get(item.id, 0.0), 1.0 / (rank + 1)
            )

    # Dense leg.
    if semantic and query.strip() and bundle.vectors:
        query_vector = bundle.query_vector(query)
        for item in items:
            vector = bundle.vectors.get(item.id)
            if vector:
                candidates[item.id].dense = max(
                    0.0, cosine(query_vector, vector)
                )

    # Path, tag, and symbol signals.
    path_ids: list[str] = []
    tag_ids: list[str] = []
    symbol_ids: list[str] = []
    for item in items:
        candidate = candidates[item.id]
        if path_set:
            candidate.path, matched = _path_hits(item, path_set)
            if matched:
                candidate.add_reason("path")
                path_ids.append(item.id)
        if tag_set:
            overlap = len(tag_set & {tag.lower() for tag in item.tags})
            candidate.tag = overlap / max(len(tag_set), 1)
            if overlap:
                candidate.add_reason("tag")
                tag_ids.append(item.id)
        if query_set and _symbol_hit(item, query_set):
            candidate.path = max(candidate.path, 0.75)
            candidate.add_reason("symbol")
            symbol_ids.append(item.id)

    for item_id, score in lexical_rank.items():
        if item_id in candidates:
            candidates[item_id].lexical = score
            candidates[item_id].add_reason("lexical")

    # Relation expansion from the strongest seeds.
    seed_ids = sorted(
        candidates,
        key=lambda item_id: (
            candidates[item_id].lexical
            + candidates[item_id].dense
            + candidates[item_id].path
            + candidates[item_id].tag
        ),
        reverse=True,
    )[:GRAPH_SEEDS]
    relations = get_relations_for_items(conn, seed_ids)
    for item_id, score in _graph_scores(seed_ids, relations).items():
        if item_id in candidates:
            candidates[item_id].graph = score
            candidates[item_id].add_reason("relation")

    # Reciprocal-rank fusion across independent legs.
    def ranked(attr: str) -> list[str]:
        return [
            item_id for item_id in sorted(
                candidates,
                key=lambda key: getattr(candidates[key], attr),
                reverse=True,
            )
            if getattr(candidates[item_id], attr) > 0
        ]

    fused = rrf_fuse([
        ranked("lexical"), ranked("dense"), ranked("path"),
        ranked("tag"), ranked("graph"),
    ])
    for item_id, candidate in candidates.items():
        candidate.fused = fused.get(item_id, 0.0)

    if not (query.strip() or tags or paths):
        # No query signals at all: return recent items rather than nothing.
        for candidate in candidates.values():
            candidate.fused = 0.05

    ordered = sorted(
        candidates.values(),
        key=lambda candidate: (
            candidate.fused, candidate.path, candidate.tag, candidate.graph,
        ),
        reverse=True,
    )
    # A claim that is not valid yet is never a candidate; ``include_expired``
    # only re-admits claims whose validity window has ended.
    ordered = [c for c in ordered if c.state != "future"]
    if not include_expired:
        ordered = [c for c in ordered if c.state != "expired"]
    if not include_stale:
        ordered = [
            c for c in ordered
            if getattr(getattr(c.item, "status", None), "value", "") != "potentially_stale"
        ]
    selected = ordered[:limit]
    meta = {
        "backend": bundle.backend,
        "model": bundle.model,
        "dim": bundle.dim,
        "total": len(items),
        "candidateCount": len(selected),
    }
    return selected, meta
