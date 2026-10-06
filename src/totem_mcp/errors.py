"""Machine-readable error taxonomy for MCP tool responses.

Tools return ``{"error": {"code", "message"}}`` so an agent can branch on the
code (retry, fix the argument, stop) instead of parsing prose.
"""

from __future__ import annotations

import json

import turso
from pydantic import ValidationError


class ErrorCode:
    NOT_FOUND = "NOT_FOUND"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    SCHEMA_ERROR = "SCHEMA_ERROR"
    CONFLICT = "CONFLICT"
    STALE = "STALE"
    DB_UNAVAILABLE = "DB_UNAVAILABLE"
    MIGRATION_FAILED = "MIGRATION_FAILED"
    INTERNAL = "INTERNAL"


def classify(exc: Exception) -> str:
    """Map an exception to an ErrorCode. Order matters (subclass checks first)."""
    if isinstance(exc, FileNotFoundError):
        return ErrorCode.NOT_FOUND
    if isinstance(exc, ValidationError):
        return ErrorCode.SCHEMA_ERROR
    if isinstance(exc, (turso.DatabaseError, turso.OperationalError, turso.InterfaceError)):
        return ErrorCode.DB_UNAVAILABLE
    text = str(exc).lower()
    if "migration" in text:
        return ErrorCode.MIGRATION_FAILED
    if "cycle" in text or "already exists" in text:
        return ErrorCode.CONFLICT
    if "stale" in text:
        return ErrorCode.STALE
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return ErrorCode.INVALID_ARGUMENT
    return ErrorCode.INTERNAL


def error_response(exc: Exception, code: str | None = None) -> str:
    """JSON error envelope for an exception."""
    payload = {"code": code or classify(exc), "message": str(exc)}
    return json.dumps({"error": payload}, indent=2)


def not_found(message: str) -> str:
    """JSON error envelope for a missing item."""
    return json.dumps({"error": {"code": ErrorCode.NOT_FOUND, "message": message}}, indent=2)
