from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import inspect

from botgraph_stream.store import Store, hash_password, verify_password


def _detection(start: float, hosts: dict[str, float], graph: dict | None = None) -> dict:  # type: ignore[type-arg]
    return {
        "sensor_id": "s1",
        "window_id": f"{int(start)}-{int(start) + 300}",
        "window_start": start,
        "window_end": start + 300,
        "n_flows": 10,
        "n_nodes": 5,
        "n_edges": 4,
        "latency_ms": 12.0,
        "hosts": [{"ip": ip, "score": sc} for ip, sc in hosts.items()],
        "graph": graph,
    }


def test_pre_migration_database_upgrades_in_place(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "legacy.db"
    # The Phase 4 schema, created without Alembic (as `create_all` used to).
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE sensors (id VARCHAR(128) PRIMARY KEY, state VARCHAR(16) NOT NULL,
            threshold FLOAT, model_threshold FLOAT, baseline_scores INTEGER NOT NULL,
            calibrated_at DATETIME, last_window_start FLOAT);
        CREATE TABLE alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, sensor_id VARCHAR(128) NOT NULL,
            ip VARCHAR(64) NOT NULL, level VARCHAR(16) NOT NULL, window_start FLOAT NOT NULL,
            score FLOAT NOT NULL, hits INTEGER NOT NULL, rule_k INTEGER NOT NULL,
            rule_n INTEGER NOT NULL, threshold FLOAT NOT NULL, explanation JSON,
            raised_at DATETIME NOT NULL, cleared_window_start FLOAT);
        CREATE TABLE window_stats (id INTEGER PRIMARY KEY AUTOINCREMENT, sensor_id VARCHAR(128) NOT NULL,
            window_id VARCHAR(64) NOT NULL, window_start FLOAT NOT NULL, n_flows INTEGER NOT NULL,
            n_nodes INTEGER NOT NULL, n_edges INTEGER NOT NULL, n_hosts INTEGER NOT NULL,
            max_score FLOAT NOT NULL, latency_ms FLOAT NOT NULL);
        INSERT INTO sensors VALUES ('old', 'active', 0.9, 0.9, 0, NULL, 100.0);
        INSERT INTO alerts (sensor_id, ip, level, window_start, score, hits, rule_k, rule_n,
            threshold, raised_at) VALUES ('old', '10.0.0.9', 'alert', 60.0, 0.99, 12, 12, 15, 0.9,
            '2026-01-01 00:00:00');
        """
    )
    con.close()

    store = Store(f"sqlite:///{path}")
    tables = set(inspect(store.engine).get_table_names())
    assert {"host_scores", "graph_snapshots", "users", "alembic_version"} <= tables
    [alert] = store.alerts()
    assert (alert.ip, alert.status) == ("10.0.0.9", "open")  # old row kept, new column defaulted
    assert store.thresholds() == {"old": 0.9}
    Store(f"sqlite:///{path}")  # opening again is a no-op


def test_windows_host_scores_snapshot_and_retention(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = Store(f"sqlite:///{tmp_path / 's.db'}", host_score_retention_s=3600)
    graph = {"nodes": [{"id": "10.0.0.1"}], "edges": []}
    for i in range(130):
        store.record_window(_detection(60.0 * i, {"10.0.0.1": i / 200, "10.0.0.2": 0.1}, graph))

    timeline = store.host_timeline("s1", "10.0.0.1")
    assert timeline[-1] == (60.0 * 129, pytest.approx(129 / 200))
    assert timeline[0][0] >= 60.0 * 129 - 3600 - 60 * 60  # pruned to ~retention (every 60 windows)
    snap = store.latest_graph("s1")
    assert snap is not None and snap.window_start == 60.0 * 129 and snap.graph == graph
    assert store.top_hosts(since=60.0 * 120, limit=1) == [
        ("s1", "10.0.0.1", pytest.approx(129 / 200))
    ]
    assert store.latest_window_start() == 60.0 * 129


def test_alert_triage_updates(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = Store(f"sqlite:///{tmp_path / 't.db'}")
    store.record_event(
        {
            "type": "alert",
            "sensor_id": "s1",
            "ip": "10.0.0.5",
            "window_start": 60.0,
            "score": 0.99,
            "hits": 12,
            "rule": {"k": 12, "n": 15},
            "threshold": 0.9,
        }
    )
    [alert] = store.alerts()
    updated = store.update_alert(
        alert.id, status="investigating", assignee="hammad", note="beacon to 6667"
    )
    assert updated is not None and (updated.status, updated.assignee) == ("investigating", "hammad")
    assert updated.updated_at is not None
    assert store.alerts(status="investigating")[0].note == "beacon to 6667"
    with pytest.raises(ValueError):
        store.update_alert(alert.id, status="deleted")
    assert store.update_alert(9999, status="resolved") is None


def test_users_and_passwords(tmp_path) -> None:  # type: ignore[no-untyped-def]
    stored = hash_password("correct horse battery")
    assert verify_password("correct horse battery", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("x", "not-a-hash")

    store = Store(f"sqlite:///{tmp_path / 'u.db'}")
    store.create_user("admin", "s3cret-pass", "admin")
    assert store.authenticate("admin", "s3cret-pass").role == "admin"  # type: ignore[union-attr]
    assert store.authenticate("admin", "nope") is None
    assert store.authenticate("ghost", "s3cret-pass") is None
    with pytest.raises(ValueError):
        store.create_user("x", "short", "admin")
    with pytest.raises(ValueError):
        store.create_user("y", "long-enough", "root")


def test_sqlite_parent_directories_are_created(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # A fresh checkout has no data/ directory (it is gitignored); CI's e2e run hit this.
    store = Store(f"sqlite:///{tmp_path / 'not' / 'yet' / 'there.db'}")
    assert store.summary()["windows"] == 0
    assert (tmp_path / "not" / "yet" / "there.db").exists()
