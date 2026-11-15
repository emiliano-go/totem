"""Calibrated trust for memory claims.

Totem's ``confidence`` is a provenance constant (who said it), not a
probability that the claim still holds.  This module turns deterministic
signals about a claim's evidence into a calibrated trust score, and reports an
honest prediction set.  It is deliberately small and dependency-free: Platt
scaling plus split-conformal prediction, both in plain Python.

Nothing here changes a verification verdict. The learned part is advisory and
starts unfitted, falling back to :func:`rule_trust`.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .hashing import check_staleness
from .paths import project_root

FEATURE_NAMES = (
    "evidence_count", "stale_fraction", "verified", "check_mode",
    "age_days", "confidence", "source_rank", "critical_type",
)

_SOURCE_RANK = {
    "user": 1.0, "test": 1.0, "source": 1.0, "git": 1.0, "doc": 1.0,
    "runtime": 0.6, "agent": 0.3,
}
_CRITICAL = {"invariant", "constraint", "contract"}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _age_days(timestamp: str | None) -> float:
    if not timestamp:
        return 180.0
    try:
        then = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return 180.0
    now = datetime.now(timezone.utc)
    return _clamp((now - then).total_seconds() / 86400.0, 0.0, 180.0)


@dataclass
class ClaimFeatures:
    evidence_count: float = 0.0
    stale_fraction: float = 0.0
    verified: float = 0.0
    check_mode: float = 0.0
    age_days: float = 0.0
    confidence: float = 0.0
    source_rank: float = 0.3
    critical_type: float = 0.0

    def as_list(self) -> list[float]:
        return [
            self.evidence_count, self.stale_fraction, self.verified,
            self.check_mode, self.age_days, self.confidence,
            self.source_rank, self.critical_type,
        ]

    def as_matrix_row(self) -> list[float]:
        return self.as_list()


def _stale(ev, root: Path | None, cache: dict | None) -> bool:
    key = (
        ev.path, ev.start_line, ev.end_line, ev.content_hash,
        ev.symbol, ev.blob_hash, ev.symbol_hash,
    )
    if cache is not None and key in cache:
        return cache[key]
    stale = check_staleness(
        Path(ev.path), ev.start_line, ev.end_line, ev.content_hash,
        symbol=ev.symbol, blob_hash=ev.blob_hash,
        symbol_hash=ev.symbol_hash, root=root,
    )
    if cache is not None:
        cache[key] = stale
    return stale


def extract_features(
    item, root: Path | None = None, stale_cache: dict | None = None
) -> ClaimFeatures:
    evidence = list(getattr(item, "evidence", None) or [])
    stale = 0
    for ev in evidence:
        if _stale(ev, root, stale_cache):
            stale += 1
    meta = getattr(item, "metadata", None) or {}
    mode = str(meta.get("verificationMode") or "assertion")
    return ClaimFeatures(
        evidence_count=min(len(evidence), 5) / 5.0,
        stale_fraction=(stale / len(evidence)) if evidence else 0.0,
        verified=1.0 if getattr(item, "verified_at", None) else 0.0,
        check_mode=1.0 if mode == "check" else 0.0,
        age_days=_age_days(getattr(item, "updated_at", None)) / 180.0,
        confidence=float(getattr(item, "confidence", 0.6)),
        source_rank=_SOURCE_RANK.get(
            str(getattr(item, "asserted_by", None) or "agent").lower(), 0.3
        ),
        critical_type=1.0 if getattr(item, "type", None) and
        item.type.value in _CRITICAL else 0.0,
    )


def rule_trust(
    item, root: Path | None = None, stale_cache: dict | None = None
) -> float:
    """Deterministic trust score, the baseline the calibrated head must beat."""
    f = extract_features(item, root=root, stale_cache=stale_cache)
    score = 0.45 * f.confidence
    score += 0.20 * f.verified
    score += 0.10 * f.check_mode
    score += 0.10 * f.source_rank
    score += 0.05 * f.evidence_count
    score -= 0.30 * f.stale_fraction
    score -= 0.10 * f.age_days
    return _clamp(score)


@dataclass
class PlattScaler:
    a: float = 1.0
    b: float = 0.0

    def fit(self, scores, labels, lr: float = 0.5, steps: int = 500) -> "PlattScaler":
        s = [float(x) for x in scores]
        y = [float(v) for v in labels]
        if not s:
            return self
        a, b = self.a, self.b
        for _ in range(steps):
            ga = gb = 0.0
            for score, label in zip(s, y):
                p = 1.0 / (1.0 + math.exp(-(a * score + b)))
                ga += (p - label) * score
                gb += p - label
            a -= lr * ga / len(s)
            b -= lr * gb / len(s)
        self.a, self.b = a, b
        return self

    def __call__(self, score: float) -> float:
        return 1.0 / (1.0 + math.exp(-(self.a * score + self.b)))

    def to_dict(self) -> dict:
        return {"a": self.a, "b": self.b}

    @classmethod
    def from_dict(cls, data: dict) -> "PlattScaler":
        return cls(a=float(data.get("a", 1.0)), b=float(data.get("b", 0.0)))

    @staticmethod
    def ece(scores, labels, bins: int = 10) -> float:
        s = [float(x) for x in scores]
        y = [float(v) for v in labels]
        if not s:
            return 0.0
        err = 0.0
        for i in range(bins):
            lo, hi = i / bins, (i + 1) / bins
            idx = [j for j, v in enumerate(s) if lo <= v < hi or (i == bins - 1 and v == hi)]
            if idx:
                confidence = sum(s[j] for j in idx) / len(idx)
                accuracy = sum(y[j] for j in idx) / len(idx)
                err += len(idx) / len(s) * abs(confidence - accuracy)
        return err


@dataclass
class ConformalCalibrator:
    """Split conformal wrapper with an honest small-sample mode."""

    base: PlattScaler = field(default_factory=PlattScaler)
    alpha: float = 0.1
    qhat: float | None = None
    mode: str = "shared"
    n: int = 0

    def __post_init__(self) -> None:
        if not 0.0 < self.alpha < 1.0:
            raise ValueError("alpha must be strictly between 0 and 1")

    def fit(self, raw, labels) -> "ConformalCalibrator":
        raw = [float(x) for x in raw]
        labels = [float(v) for v in labels]
        n = len(raw)
        self.n = n
        if n >= 40:
            cut = n // 2
            base_raw, base_y = raw[:cut], labels[:cut]
            score_raw, score_y = raw[cut:], labels[cut:]
            self.mode = "split"
        else:
            base_raw, base_y = raw, labels
            score_raw, score_y = raw, labels
            self.mode = "shared"
        self.base.fit(base_raw, base_y)
        p = [self.base(v) for v in score_raw]
        nonconformity = [
            1.0 - (pi if yi > 0.5 else 1.0 - pi)
            for pi, yi in zip(p, score_y)
        ]
        m = len(nonconformity)
        if m:
            k = max(1, math.ceil((m + 1) * (1.0 - self.alpha)))
            # k > m means the requested confidence exceeds what m samples can
            # certify; the conformal threshold is infinite (the full set).
            self.qhat = 1.0 if k > m else sorted(nonconformity)[k - 1]
        return self

    def prob(self, raw: float) -> float:
        return self.base(raw)

    def predict_set(self, raw: float) -> set[int]:
        if self.qhat is None:
            return {0, 1}
        p = self.base(raw)
        out: set[int] = set()
        if 1.0 - p <= self.qhat:
            out.add(1)
        if p <= self.qhat:
            out.add(0)
        return out or {1 if p >= 0.5 else 0}

    def coverage(self, raw, labels) -> float:
        raw = [float(x) for x in raw]
        labels = [float(v) for v in labels]
        if not raw:
            return 0.0
        hits = sum(1 for r, y in zip(raw, labels) if int(y > 0.5) in self.predict_set(r))
        return hits / len(raw)

    def to_dict(self) -> dict:
        return {
            "base": self.base.to_dict(), "alpha": self.alpha,
            "qhat": self.qhat, "mode": self.mode, "n": self.n,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ConformalCalibrator":
        cal = cls(
            base=PlattScaler.from_dict(data.get("base") or {}),
            alpha=float(data.get("alpha", 0.1)),
        )
        cal.qhat = data.get("qhat")
        cal.mode = str(data.get("mode", "shared"))
        cal.n = int(data.get("n", 0))
        return cal


def brier(scores, labels) -> float:
    s = [float(x) for x in scores]
    y = [float(v) for v in labels]
    return sum((a - b) ** 2 for a, b in zip(s, y)) / len(s) if s else 0.0


def wilson_ci(successes: int, n: int, z: float = 1.96) -> list[float]:
    if n <= 0:
        return [0.0, 1.0]
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [max(0.0, centre - margin), min(1.0, centre + margin)]


def temporal_split(rows, train_frac: float = 0.6, cal_frac: float = 0.2):
    """Split time-ordered rows into (train, calibration, test)."""
    n = len(rows)
    cut = int(n * train_frac)
    cut2 = int(n * (train_frac + cal_frac))
    return rows[:cut], rows[cut:cut2], rows[cut2:]


def label_for(item, root: Path | None = None) -> int:
    """A claim is positive while it is live and its evidence still matches."""
    status = getattr(getattr(item, "status", None), "value", "active")
    if status in {"invalidated", "superseded", "resolved", "deleted"}:
        return 0
    if rule_trust(item, root=root) < 0.35:
        return 0
    return 1


def fit_calibrator(items, root: Path | None = None) -> ConformalCalibrator | None:
    rows = []
    for item in items:
        rows.append((getattr(item, "updated_at", "") or "", rule_trust(item, root), label_for(item, root)))
    rows.sort(key=lambda r: r[0])
    if len(rows) < 8 or len({r[2] for r in rows}) < 2:
        return None
    train, cal, _test = temporal_split(rows)
    fit_rows = train + cal
    raw = [r[1] for r in fit_rows]
    labels = [r[2] for r in fit_rows]
    return ConformalCalibrator(alpha=0.1).fit(raw, labels)


def trust_report(item, calibrator: ConformalCalibrator | None = None,
                 root: Path | None = None) -> dict:
    raw = rule_trust(item, root=root)
    out = {
        "rule": round(raw, 4),
        "features": dict(zip(FEATURE_NAMES, (round(v, 4) for v in extract_features(item, root).as_list()))),
    }
    if calibrator is not None and calibrator.qhat is not None:
        out["calibrated"] = round(calibrator.prob(raw), 4)
        out["set"] = sorted(calibrator.predict_set(raw))
        out["mode"] = calibrator.mode
    else:
        out["calibrated"] = None
        out["set"] = [1 if raw >= 0.5 else 0]
        out["mode"] = "rule"
    return out


def load_calibrator(conn) -> ConformalCalibrator | None:
    from .db import get_meta

    raw = get_meta(conn, "calibration:model")
    if not raw:
        return None
    try:
        return ConformalCalibrator.from_dict(json.loads(raw))
    except (ValueError, TypeError):
        return None


def save_calibrator(conn, calibrator: ConformalCalibrator | None) -> None:
    from .db import set_meta

    if calibrator is None:
        return
    set_meta(conn, "calibration:model", json.dumps(calibrator.to_dict(), sort_keys=True))


def validate_and_promote(conn, min_test: int = 20, tolerance: float = 0.1) -> dict:
    """Gate the calibrated head on a temporal held-out slice.

    Requires both classes, enough test rows, a lower Brier score than the rule,
    and conformal coverage within tolerance. The verdict is keyed by the git
    commit and history length so it expires when the tree or log moves on.
    """
    from .db import get_all_items, get_meta, get_head_commit, set_meta

    root = project_root(conn)
    items = get_all_items(conn)
    rows = [
        (getattr(i, "updated_at", "") or "", i, rule_trust(i, root), label_for(i, root))
        for i in items
    ]
    rows.sort(key=lambda r: r[0])
    labels = [r[3] for r in rows]
    reasons: list[str] = []
    test = temporal_split(rows)[2]
    if len(rows) < 20:
        reasons.append("insufficient_history")
    if len(set(labels)) < 2:
        reasons.append("needs_both_classes")
    if len(test) < min_test:
        reasons.append(f"test_n_below_{min_test}")

    calibrator = None
    if not reasons:
        train, cal, _test = temporal_split(rows)
        fit_rows = train + cal
        calibrator = ConformalCalibrator(alpha=0.1).fit(
            [r[2] for r in fit_rows], [r[3] for r in fit_rows]
        )
        test_raw = [r[2] for r in test]
        test_labels = [r[3] for r in test]
        learned = brier([calibrator.prob(v) for v in test_raw], test_labels)
        rule = brier(test_raw, test_labels)
        coverage = calibrator.coverage(test_raw, test_labels)
        if learned >= rule:
            reasons.append("learned_brier_not_better_than_rule")
        if coverage < 1.0 - calibrator.alpha - tolerance:
            reasons.append("coverage_below_target")
    else:
        learned = rule = coverage = None

    promoted = not reasons
    verdict = {
        "promoted": promoted,
        "reasons": reasons,
        "rows": len(rows),
        "test_rows": len(test),
        "learned_brier": learned,
        "rule_brier": rule,
        "coverage": coverage,
        "commit": get_head_commit(),
    }
    set_meta(conn, "calibration:verdict", json.dumps(verdict, sort_keys=True))
    if promoted and calibrator is not None:
        save_calibrator(conn, calibrator)
    return verdict
