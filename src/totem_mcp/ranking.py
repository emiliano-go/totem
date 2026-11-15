"""Dependency-free retrieval models for the context compiler.

Three pieces, all deterministic and pure Python:

* :class:`DenseEncoder` - signed feature-hashing TF-IDF over word tokens plus
  character trigrams.  A fixed blake2b hash keeps rankings identical across
  processes (Python's ``hash`` is salted per process).
* :func:`rrf_fuse` - reciprocal-rank fusion of a lexical and a dense ranking.
* :func:`mmr` - maximal marginal relevance, so context is not five
  near-duplicate memories.

Plus :class:`Reranker`, a linear model over cheap pairwise features that falls
back to a transparent heuristic until it is trained.
"""

from __future__ import annotations

import hashlib
import math
import re

from .embeddings import get_static_encoder

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
DEFAULT_DIM = 256


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for word in _WORD.findall(text or ""):
        out.append(word.lower())
        parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", word)
        if len(parts) > 1:
            out.extend(part.lower() for part in parts)
    return out


def _features(text: str) -> list[str]:
    words = tokenize(text)
    out = list(words)
    for word in words:
        if len(word) >= 5:
            out.extend("~" + word[i:i + 3] for i in range(len(word) - 2))
    return out


def _stable_hash(token: str, dim: int) -> tuple[int, float]:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "little")
    return value % dim, (1.0 if (value >> 63) & 1 else -1.0)


class DenseEncoder:
    """Signed feature-hashing TF-IDF encoder producing L2-normalised vectors."""

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        self.dim = dim
        self.idf: dict[str, float] = {}

    def fit(self, texts: list[str]) -> "DenseEncoder":
        n = len(texts)
        df: dict[str, int] = {}
        for text in texts:
            for token in set(_features(text)):
                df[token] = df.get(token, 0) + 1
        self.idf = {
            token: math.log((1 + n) / (1 + count)) + 1.0
            for token, count in df.items()
        }
        return self

    def encode(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _features(text):
            index, sign = _stable_hash(token, self.dim)
            vec[index] += sign * self.idf.get(token, 1.0)
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec

    def rank(self, query: str, texts: list[str]) -> list[tuple[int, float]]:
        q = self.encode(query)
        scored = []
        for i, text in enumerate(texts):
            v = self.encode(text)
            sim = sum(a * b for a, b in zip(q, v))
            if sim > 0:
                scored.append((i, sim))
        scored.sort(key=lambda kv: -kv[1])
        return scored

    def vectors(self, texts: list[str]) -> list[list[float]]:
        return [self.encode(t) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def semantic_vectors(texts: list[str], query: str) -> tuple[list[list[float]], list[float], str]:
    """Document and query vectors from the configured encoder.

    Returns ``(document_vectors, query_vector, backend)`` where backend is
    ``model2vec`` when the optional static encoder is active, otherwise
    ``hash`` for the dependency-free fallback.
    """
    static = get_static_encoder()
    if static is not None:
        return static.encode_many(texts), static.encode(query), "model2vec"
    encoder = DenseEncoder().fit(texts + [query])
    return encoder.vectors(texts), encoder.encode(query), "hash"


def rrf_fuse(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal-rank fusion; returns a normalised score per id."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item_id in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    if not scores:
        return {}
    top = max(scores.values()) or 1.0
    return {key: value / top for key, value in scores.items()}


def mmr(query_vec: list[float], doc_vecs: list[list[float]],
        indices: list[int], k: int, lam: float = 0.7) -> list[int]:
    """Greedy MMR ordering of candidate indices (best-first input)."""
    cand = list(dict.fromkeys(indices))
    if not cand:
        return []
    rel = {i: cosine(doc_vecs[i], query_vec) for i in cand}
    selected: list[int] = []
    while cand and len(selected) < k:
        best, best_score = None, -math.inf
        for i in cand:
            redundancy = (
                max(cosine(doc_vecs[i], doc_vecs[j]) for j in selected)
                if selected else 0.0
            )
            score = lam * rel[i] - (1.0 - lam) * redundancy
            if score > best_score:
                best, best_score = i, score
        selected.append(best)
        cand.remove(best)
    return selected


def _solve(matrix: list[list[float]], rhs: list[float], ridge: float = 1e-6) -> list[float]:
    """Gaussian elimination with partial pivoting (small, well-conditioned)."""
    n = len(rhs)
    a = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for i in range(n):
        a[i][i] += ridge
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return [0.0] * n
        a[col], a[pivot] = a[pivot], a[col]
        for row in range(col + 1, n):
            factor = a[row][col] / a[col][col]
            for j in range(col, n + 1):
                a[row][j] -= factor * a[col][j]
    out = [0.0] * n
    for row in range(n - 1, -1, -1):
        total = a[row][n] - sum(a[row][j] * out[j] for j in range(row + 1, n))
        out[row] = total / a[row][row]
    return out


class Reranker:
    """Linear pairwise reranker over query/item features.

    Features: token coverage, log overlap, symbol hit, path hit, length norm,
    verified flag, staleness penalty. Untrained it returns the transparent
    weighted sum; trained it uses least-squares weights.
    """

    def __init__(self, weights: list[float] | None = None) -> None:
        self.weights = weights

    @staticmethod
    def features(query: str, text: str, path: str = "", symbol: str = "",
                 verified: bool = False, stale: bool = False) -> list[float]:
        q = set(tokenize(query))
        d = tokenize(text)
        dset = set(d)
        overlap = len(q & dset)
        coverage = overlap / (len(q) or 1)
        symbol_hit = 1.0 if symbol and symbol.lower() in q else 0.0
        path_hit = 1.0 if path and any(t in path.lower() for t in q) else 0.0
        return [
            coverage, math.log1p(overlap), symbol_hit, path_hit,
            min(len(d), 400) / 400.0, 1.0 if verified else 0.0,
            -1.0 if stale else 0.0,
        ]

    def score(self, features: list[float]) -> float:
        if self.weights is None:
            return features[0] + features[2] + features[3] + 0.1 * features[5] + 0.1 * features[6]
        return sum(w * f for w, f in zip(self.weights, features))

    def train(self, examples: list[tuple[list[float], float]]) -> "Reranker":
        if not examples:
            return self
        n = len(examples[0][0])
        xtx = [[0.0] * n for _ in range(n)]
        xty = [0.0] * n
        for features, label in examples:
            for i in range(n):
                xty[i] += features[i] * label
                for j in range(n):
                    xtx[i][j] += features[i] * features[j]
        self.weights = _solve(xtx, xty)
        return self

    def to_dict(self) -> dict:
        return {"weights": self.weights}

    @classmethod
    def from_dict(cls, data: dict) -> "Reranker":
        return cls(weights=data.get("weights"))


MEMORY_FEATURES = (
    "lexical", "dense", "fused", "tag", "path", "graph", "trust",
    "importance", "verified", "recency", "critical", "stale",
    "validity", "confidence",
)


class MemoryReranker:
    """Linear reranker over candidate and trust features.

    Untrained it is a transparent weighted heuristic; trained it uses
    least-squares weights learned from feedback.
    """

    version = "memory-heuristic-v1"

    def __init__(self, weights: list[float] | None = None) -> None:
        self.weights = weights
        if weights is not None:
            self.version = "memory-linear-v1"

    @staticmethod
    def features(candidate, trust: float, now=None) -> list[float]:
        item = candidate.item
        updated = getattr(item, "updated_at", "") or ""
        age_days = 30.0
        if updated:
            from datetime import datetime, timezone
            try:
                then = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                age_days = max(
                    0.0,
                    ((now or datetime.now(timezone.utc)) - then).total_seconds() / 86400.0,
                )
            except ValueError:
                age_days = 30.0
        status = getattr(getattr(item, "status", None), "value", "active")
        critical = 1.0 if (
            getattr(item, "type", None)
            and item.type.value in ("invariant", "constraint", "contract")
        ) else 0.0
        return [
            candidate.lexical,
            candidate.dense,
            candidate.fused,
            candidate.tag,
            candidate.path,
            candidate.graph,
            trust,
            float(getattr(item, "importance", 0.5) or 0.5),
            1.0 if getattr(item, "verified_at", None) else 0.0,
            1.0 / (1.0 + age_days / 30.0),
            critical,
            1.0 if status == "potentially_stale" else 0.0,
            candidate.validity,
            float(getattr(item, "confidence", 0.6) or 0.6),
        ]

    def score(self, features: list[float]) -> float:
        if self.weights is not None and len(self.weights) == len(features):
            return sum(w * f for w, f in zip(self.weights, features))
        f = features
        return (
            0.30 * f[2] + 0.15 * f[0] + 0.10 * f[1] + 0.15 * f[3]
            + 0.10 * f[4] + 0.05 * f[5] + 0.10 * f[6] + 0.05 * f[7]
            + 0.05 * f[8] + 0.03 * f[9] + 0.02 * f[10] - 0.15 * f[11]
            + 0.03 * f[12]
        )

    def train(self, examples: list[tuple[list[float], float]]) -> "MemoryReranker":
        if not examples:
            return self
        n = len(examples[0][0])
        xtx = [[0.0] * n for _ in range(n)]
        xty = [0.0] * n
        for features, label in examples:
            for i in range(n):
                xty[i] += features[i] * label
                for j in range(n):
                    xtx[i][j] += features[i] * features[j]
        self.weights = _solve(xtx, xty)
        self.version = "memory-linear-v1"
        return self

    def to_dict(self) -> dict:
        return {"weights": self.weights, "version": self.version}

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryReranker":
        reranker = cls(weights=data.get("weights"))
        reranker.version = str(data.get("version", reranker.version))
        return reranker


def experience_nearest(query: str, texts: list[str], ids: list[str]) -> str | None:
    """The single most similar prior item (SWE-Exp N=1 retrieval)."""
    if not query or not texts:
        return None
    vectors, query_vector, _backend = semantic_vectors(texts, query)
    scores = [cosine(query_vector, vector) for vector in vectors]
    best = max(range(len(scores)), key=lambda index: scores[index], default=None)
    if best is None or scores[best] <= 0:
        return None
    return ids[best]
