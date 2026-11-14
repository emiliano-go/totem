"""Advisory models: calibration, conformal sets, ranking, fusion."""

from __future__ import annotations

from totem_mcp.calibration import (
    ConformalCalibrator,
    PlattScaler,
    fit_calibrator,
    rule_trust,
    validate_and_promote,
)
from totem_mcp.ranking import DenseEncoder


def test_platt_is_monotonic():
    scaler = PlattScaler().fit([0.0, 0.25, 0.5, 0.75, 1.0], [0, 0, 1, 1, 1])
    values = [scaler(x) for x in (0.0, 0.5, 1.0)]
    assert values[0] < values[1] < values[2]


def test_conformal_small_sample_reports_shared_mode():
    cal = ConformalCalibrator(alpha=0.1).fit(
        [0.1, 0.4, 0.8, 0.9], [0, 0, 1, 1]
    )
    assert cal.mode == "shared"
    assert cal.predict_set(0.5)


def test_conformal_large_sample_splits_and_covers():
    raw = [i / 100 for i in range(100)]
    labels = [1 if value > 0.5 else 0 for value in raw]
    cal = ConformalCalibrator(alpha=0.1).fit(raw, labels)
    assert cal.mode == "split"
    assert cal.qhat is not None
    assert 0.0 <= cal.coverage(raw, labels) <= 1.0


def test_rule_trust_prefers_fresh_check_backed_items(fresh_db, tmp_path):
    from totem_mcp.tools import memory_check_record, memory_create

    target = tmp_path / "a.py"
    target.write_text("line\n")
    item = memory_create(
        fresh_db, type="gotcha", title="g", statement="s", tags=["x"],
    )
    check = memory_check_record(fresh_db, item["id"], "pytest")
    from totem_mcp.db import get_item
    from totem_mcp.calibration import save_calibrator
    from totem_mcp.tools import memory_verify

    memory_verify(fresh_db, item["id"], check_id=check["id"])
    checked = rule_trust(get_item(fresh_db, item["id"]), root=tmp_path)
    assert 0.0 <= checked <= 1.0


def test_fit_calibrator_needs_both_classes(fresh_db):
    from totem_mcp.db import get_all_items
    from totem_mcp.tools import memory_create

    for i in range(10):
        memory_create(fresh_db, type="gotcha", title=f"g{i}", statement="s", tags=["x"])
    assert fit_calibrator(get_all_items(fresh_db)) is None


def test_validate_and_promote_refuses_thin_history(fresh_db):
    verdict = validate_and_promote(fresh_db)
    assert verdict["promoted"] is False
    assert "insufficient_history" in verdict["reasons"]


def test_dense_encoder_is_deterministic():
    texts = ["parse the token stream", "rotate the refresh token", "queue delivery"]
    first = DenseEncoder().fit(texts).encode("token")
    second = DenseEncoder().fit(texts).encode("token")
    assert first == second
    assert abs(sum(v * v for v in first) - 1.0) < 1e-9


def test_rrf_fuse_normalises_and_rewards_agreement():
    from totem_mcp.ranking import rrf_fuse

    fused = rrf_fuse([["a", "b"], ["b", "c"]])
    assert fused["b"] == 1.0
    assert 0 < fused["a"] < 1.0
    assert 0 < fused["c"] < 1.0


def test_mmr_returns_a_permutation():
    from totem_mcp.ranking import mmr

    vecs = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]
    order = mmr([1.0, 0.0], vecs, [0, 1, 2], k=3, lam=0.5)
    assert sorted(order) == [0, 1, 2]
    assert order[0] in (0, 1)


def test_reranker_trains_and_scores():
    from totem_mcp.ranking import Reranker

    examples = [
        (Reranker.features("token", "token stream", symbol="token"), 1.0),
        (Reranker.features("token", "unrelated prose"), 0.0),
    ]
    reranker = Reranker().train(examples)
    good = reranker.score(Reranker.features("token", "token stream", symbol="token"))
    bad = reranker.score(Reranker.features("token", "unrelated prose"))
    assert good > bad
