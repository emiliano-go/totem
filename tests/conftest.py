"""Shared fixtures: scratch Turso DBs under tmp_path only."""

from __future__ import annotations

import pytest

import totem_mcp.context as context_mod
import totem_mcp.db as db_mod
from totem_mcp.db import connect, init_db


@pytest.fixture(autouse=True)
def _isolate_home_and_project_init(monkeypatch, tmp_path):
    """Never touch real user DB or real home dir during tests.

    - engineering_context reads ~/.local/share/totem/totem.db; point it at a
      nonexistent tmp path (tests can re-patch for the corrupt-user-db case).
    - db_connection() calls init_project() on first use, which appends agent
      config to ~/.config/opencode and project AGENTS.md; make it a no-op.
    """
    monkeypatch.setattr(
        context_mod, "get_user_db_path", lambda: tmp_path / "no-user-db" / "totem.db"
    )
    monkeypatch.setattr(
        db_mod, "init_project", lambda project_dir: {"path": str(project_dir)}
    )


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "totem.db"


@pytest.fixture
def fresh_db(db_path):
    """Fresh initialized Turso DB connection; closed after the test."""
    conn = connect(db_path=db_path)
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def project_dir(tmp_path):
    """A tmp directory usable as a --project root (no git needed: explicit
    project paths bypass git-root detection in get_db_path)."""
    proj = tmp_path / "proj"
    proj.mkdir()
    return proj


@pytest.fixture
def sample_file(tmp_path):
    """A small text file for register_file_read/write tests."""
    f = tmp_path / "sample.py"
    f.write_text("line one\nline two\nline three\nline four\nline five\n")
    return f
