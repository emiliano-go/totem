"""Real multi-process concurrency tests.

Each worker opens its own connection to the same on-disk DB and races the others
through a barrier. These exercise the cross-process write lock (`BEGIN
IMMEDIATE` + retry), the unique locator identity, and migration convergence.
"""

from __future__ import annotations

import multiprocessing as mp

from totem_mcp import db as db_mod
from totem_mcp import tools as tools_mod
from totem_mcp.db import connect, init_db

N = 8


def _runner(q, barrier, target, args):
    try:
        barrier.wait(timeout=30)
        q.put(("ok", target(*args)))
    except BaseException as exc:  # noqa: BLE001
        q.put(("err", repr(exc)))


def _spawn(target, arg_tuples):
    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(len(arg_tuples))
    q = ctx.Queue()
    procs = [
        ctx.Process(target=_runner, args=(q, barrier, target, args))
        for args in arg_tuples
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
    results = []
    while not q.empty():
        results.append(q.get())
    assert all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs]
    return results


def _query(db_path, sql, params=()):
    conn = connect(db_path=db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _fresh(db_path):
    conn = connect(db_path=db_path)
    init_db(conn)
    conn.close()


def _create_worker(db_path, idx):
    conn = connect(db_path=db_path)
    try:
        return tools_mod.memory_create(
            conn,
            type="gotcha",
            title=f"item-{idx}",
            statement=f"statement {idx}",
            tags=["concurrency"],
        )
    finally:
        conn.close()


def _same_op_worker(db_path):
    conn = connect(db_path=db_path)
    try:
        return tools_mod.memory_create(
            conn,
            type="gotcha",
            title="shared",
            statement="shared statement",
            tags=["concurrency"],
            operation_id="op-shared",
        )
    finally:
        conn.close()


def _init_worker(db_path):
    conn = connect(db_path=db_path)
    try:
        init_db(conn)
        return db_mod._schema_version(conn)
    finally:
        conn.close()


def _read_worker(db_path, file_path):
    conn = connect(db_path=db_path)
    try:
        return tools_mod.register_file_read(
            conn,
            path=str(file_path),
            statement="fact",
            subject="shared",
            kind="function",
            tags=["concurrency"],
        )
    finally:
        conn.close()


def _write_worker(db_path, file_path):
    conn = connect(db_path=db_path)
    try:
        return tools_mod.register_file_write(
            conn,
            path=str(file_path),
            statement="wrote",
            reason="test",
            tags=["concurrency"],
        )
    finally:
        conn.close()


def test_concurrent_creates(tmp_path):
    db_path = tmp_path / "totem.db"
    _fresh(db_path)

    results = _spawn(_create_worker, [(db_path, i) for i in range(N)])

    assert all(kind == "ok" for kind, _ in results), results
    assert len({r["id"] for _, r in results}) == N
    assert _query(db_path, "SELECT COUNT(*) FROM memory_items")[0][0] == N


def test_same_operation_id_runs_once(tmp_path):
    db_path = tmp_path / "totem.db"
    _fresh(db_path)

    results = _spawn(_same_op_worker, [(db_path,)] * N)

    assert all(kind == "ok" for kind, _ in results), results
    ids = {r["id"] for _, r in results}
    assert len(ids) == 1
    assert _query(db_path, "SELECT COUNT(*) FROM memory_items")[0][0] == 1
    assert sum(1 for _, r in results if r.get("replayed")) == N - 1


def test_simultaneous_migration_converges(tmp_path):
    db_path = tmp_path / "totem.db"  # never initialized: all workers bootstrap

    results = _spawn(_init_worker, [(db_path,)] * N)

    assert all(kind == "ok" for kind, _ in results), results
    assert {version for _, version in results} == {db_mod.SCHEMA_VERSION}
    names = {r[0] for r in _query(db_path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"memory_items", "memory_locators", "memory_operations", "meta"} <= names


def test_parallel_register_file_read_same_fact(tmp_path, sample_file):
    db_path = tmp_path / "totem.db"
    _fresh(db_path)

    results = _spawn(_read_worker, [(db_path, sample_file)] * N)

    assert all(kind == "ok" for kind, _ in results), results
    assert len({r["id"] for _, r in results}) == 1
    assert _query(
        db_path, "SELECT COUNT(*) FROM memory_items WHERE type = 'implementation'"
    )[0][0] == 1
    assert _query(
        db_path, "SELECT COUNT(*) FROM memory_locators WHERE path = ?", (str(sample_file),)
    )[0][0] == 1


def test_parallel_register_file_write_same_path(tmp_path, sample_file):
    db_path = tmp_path / "totem.db"
    _fresh(db_path)

    results = _spawn(_write_worker, [(db_path, sample_file)] * N)

    assert all(kind == "ok" for kind, _ in results), results
    assert len({r["id"] for _, r in results}) == 1
    assert _query(
        db_path, "SELECT COUNT(*) FROM memory_items WHERE type = 'implementation'"
    )[0][0] == 1
