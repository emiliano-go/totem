"""MCP hardening: output caps, rate limits, arg bounds, manifest, timeout."""

from __future__ import annotations

import json
import time

import totem_mcp.hardening as hardening


def test_output_is_bounded(monkeypatch):
    monkeypatch.setattr(hardening, "MAX_OUTPUT_BYTES", 64)

    @hardening.harden_tool
    def big():
        return "x" * 1000

    payload = json.loads(big())
    assert payload["truncated"] is True
    assert payload["limit"] == 64
    assert len(payload["preview"].encode()) <= 8192


def test_rate_limit_fails_soft(monkeypatch):
    monkeypatch.setattr(hardening, "RATE_PER_MIN", 0.0)
    monkeypatch.setattr(hardening, "RATE_BURST", 1.0)
    hardening._RATE.clear()

    @hardening.harden_tool
    def once():
        return "ok"

    assert once() == "ok"
    second = json.loads(once())
    assert "rate limit" in second["error"]


def test_oversized_argument_is_rejected(monkeypatch):
    monkeypatch.setattr(hardening, "MAX_ARG_STRING", 8)

    @hardening.harden_tool
    def echo(text: str):
        return text

    assert "exceeds" in json.loads(echo(text="x" * 20))["error"]


def test_deadline_returns_an_error(monkeypatch):
    monkeypatch.setattr(hardening, "TOOL_TIMEOUT_MS", 20)

    @hardening.harden_tool
    def slow():
        time.sleep(0.2)
        return "late"

    assert "timed out" in json.loads(slow())["error"]


def test_manifest_is_stable_and_counts_tools():
    class Tool:
        def __init__(self, name):
            self.description = f"{name} desc"
            self.parameters = {"type": "object", "properties": {}}

    tools = {"b": Tool("b"), "a": Tool("a")}
    first = hardening.tool_manifest(tools)
    assert first == hardening.tool_manifest(tools)
    assert first["tools"] == 2
    assert len(first["sha256"]) == 64
