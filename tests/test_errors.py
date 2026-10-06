"""Typed error envelopes for MCP tool responses."""

from __future__ import annotations

import json

from pydantic import ValidationError

from totem_mcp.errors import ErrorCode, classify, error_response, not_found
from totem_mcp.models import MemoryItem


def test_classify_maps_exceptions_to_codes():
    assert classify(FileNotFoundError("x")) == ErrorCode.NOT_FOUND
    assert classify(ValueError("x")) == ErrorCode.INVALID_ARGUMENT
    assert classify(TypeError("x")) == ErrorCode.INVALID_ARGUMENT
    assert classify(KeyError("x")) == ErrorCode.INVALID_ARGUMENT
    assert classify(RuntimeError("migration v5 postcondition failed")) == ErrorCode.MIGRATION_FAILED
    assert classify(RuntimeError("boom")) == ErrorCode.INTERNAL


def test_schema_errors_are_classified():
    try:
        MemoryItem(type="invariant", title="t", statement="s", tags=["x"], metadata={})
    except ValidationError as exc:
        assert classify(exc) == ErrorCode.SCHEMA_ERROR
    else:  # pragma: no cover
        raise AssertionError("expected ValidationError")


def test_error_response_envelope_is_machine_readable():
    out = json.loads(error_response(ValueError("bad arg")))
    assert out == {"error": {"code": "INVALID_ARGUMENT", "message": "bad arg"}}


def test_not_found_helper():
    out = json.loads(not_found("Item x not found"))
    assert out["error"]["code"] == "NOT_FOUND"
    assert out["error"]["message"] == "Item x not found"
