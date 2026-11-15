"""Context ranking evaluation and promotion gate."""

from __future__ import annotations

from totem_mcp.eval_bench import Case, evaluate, gate


def _runner(case):
    table = {
        "a": ["a", "b", "c"],
        "b": ["b", "a", "c"],
        "c": ["c"],
    }
    return {"selectedIds": table[case.name]}


def test_evaluate_reports_recall_and_mrr():
    cases = [Case("a", expected=["a"]), Case("b", expected=["a"])]
    report = evaluate(cases, _runner, k=3)
    assert report["n"] == 2
    assert report["recall_at_3"] == 1.0
    assert 0.0 < report["mrr"] < 1.0


def test_evaluate_survives_a_failing_case():
    def boom(case):
        raise RuntimeError("nope")

    report = evaluate([Case("a", expected=["a"])], boom)
    assert report["recall_at_10"] == 0.0
    assert "error" in report["cases"][0]


def test_gate_detects_regression_and_latency():
    baseline = {"recall_at_10": 0.9, "mrr": 0.8}
    good = {"recall_at_10": 0.89, "mrr": 0.8, "p95_ms": 10}
    assert gate(baseline, good)["ok"] is True

    bad = {"recall_at_10": 0.5, "mrr": 0.8, "p95_ms": 10}
    verdict = gate(baseline, bad)
    assert verdict["ok"] is False
    assert "recall_regression" in verdict["failures"]

    slow = {"recall_at_10": 0.9, "mrr": 0.8, "p95_ms": 5000}
    assert "latency_budget" in gate(baseline, slow, p95_ms=2000)["failures"]


def test_abstention_cases_and_ndcg():
    cases = [
        Case("none", kind="abstention", expected=[]),
        Case("hit", expected=["a"]),
    ]

    def runner(case):
        if case.kind == "abstention":
            return {"abstained": True, "selectedIds": []}
        return {"selectedIds": ["a", "b"]}

    report = evaluate(cases, runner, k=10)
    assert report["abstention_cases"] == 1
    assert report["abstention_accuracy"] == 1.0
    assert report["ndcg_at_10"] == 1.0


def test_abstention_failure_is_counted():
    report = evaluate(
        [Case("none", kind="abstention", expected=[])],
        lambda _case: {"abstained": False, "selectedIds": ["a"]},
    )
    assert report["abstention_accuracy"] == 0.0
