"""Property-based invariants for paths, fusion, and output bounds."""

from __future__ import annotations

import os

from hypothesis import given, settings
from hypothesis import strategies as st

from totem_mcp.hardening import _bounded
from totem_mcp.paths import EvidencePathError, resolve_within
from totem_mcp.ranking import rrf_fuse

_SEGMENT = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=10
)
_SAFE_PATH = st.lists(_SEGMENT, min_size=1, max_size=4).map(
    lambda parts: os.path.join(*parts)
)


@settings(max_examples=100)
@given(_SAFE_PATH)
def test_contained_relative_paths_stay_under_root(rel):
    root = os.path.abspath(os.sep + "totem-root")
    got = resolve_within(root, rel)
    assert os.path.realpath(got) == os.path.realpath(os.path.join(root, rel))
    assert os.path.commonpath(
        [os.path.realpath(got), os.path.realpath(root)]
    ) == os.path.realpath(root)


@settings(max_examples=50)
@given(st.lists(st.just(".."), min_size=1, max_size=4), _SAFE_PATH)
def test_parent_traversal_is_always_rejected(ups, tail):
    root = os.path.abspath(os.sep + "totem-root")
    try:
        resolve_within(root, os.path.join(*ups, tail))
    except EvidencePathError:
        return
    raise AssertionError("traversal was accepted")


@settings(max_examples=100)
@given(st.lists(st.text(min_size=1, max_size=8), min_size=1, max_size=5))
def test_rrf_scores_stay_normalised(ranking):
    fused = rrf_fuse([ranking])
    assert fused
    assert all(0.0 < score <= 1.0 for score in fused.values())
    assert max(fused.values()) == 1.0


@settings(max_examples=50)
@given(st.text(max_size=2000))
def test_bounded_output_never_exceeds_envelope(text):
    # _bounded either returns the text unchanged or a bounded JSON envelope.
    out = _bounded(text)
    if out != text:
        import json
        payload = json.loads(out)
        assert payload["truncated"] is True
        assert len(payload["preview"].encode("utf-8")) <= 8192
