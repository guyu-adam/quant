import json
import sqlite3
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from q6.sim import layout
from q6.sim.web import create_app


@pytest.fixture
def fixture_saves(tmp_path):
    s = tmp_path / "saves"
    for d in ("runs", "archive", "hb", "logs"):
        (s / d).mkdir(parents=True)
    db = s / "runs" / "x.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript(layout.SCHEMA)
        conn.executemany(
            "INSERT INTO meta VALUES (?,?)", [("status", "running"), ("spec", json.dumps({"a": 1}))]
        )
        conn.executemany(
            "INSERT INTO daily(date,equity) VALUES (?,?)", [("2024-01-01", 100), ("2024-01-02", 90)]
        )
        conn.execute("INSERT INTO positions VALUES ('ABC',10,5,8,9)")
        conn.execute("INSERT INTO fills(seq,ts,symbol,side,qty,price) VALUES (1,'now','ABC','BUY',10,9)")
        conn.execute("INSERT INTO events(ts,kind,msg) VALUES ('now','test','event')")
    (s / "logs" / "x.log").write_text("log\n")
    now = datetime.now(UTC).isoformat()
    layout.write_json_atomic(
        s / "supervisor.json",
        {
            "pid": 1,
            "started_at": now,
            "updated_at": now,
            "workers": [
                {
                    "run_id": "x",
                    "state": "running",
                    "n_days": 2,
                    "total_days": 2,
                    "hb_age_s": 1,
                    "peak_mb": 20,
                }
            ],
            "queue": [],
            "done": [],
        },
    )
    return s


@pytest.fixture
def client(fixture_saves):
    return TestClient(create_app(fixture_saves))


def test_index_has_worker(client):
    r = client.get("/")
    assert r.status_code == 200 and "x" in r.text and "Workers" in r.text and "equity" in r.text


def test_run_has_svg_positions_and_fills(client):
    r = client.get("/run/x")
    assert r.status_code == 200 and "<polyline" in r.text and "当前持仓" in r.text and "最近成交" in r.text


def test_rejects_bad_names(client):
    assert client.get("/logs/..%2F..%2Fx").status_code in (400, 404)
    assert client.get("/run/..%2Fsecret").status_code in (400, 404)


def test_health_bad_supervisor_heartbeat_peak(client, fixture_saves):
    layout.write_json_atomic(
        fixture_saves / "supervisor.json",
        {
            "updated_at": "2000-01-01T00:00:00+00:00",
            "workers": [{"run_id": "x", "hb_age_s": 61, "peak_mb": 513}],
        },
    )
    data = client.get("/healthz").json()
    assert data["ok"] is False and data["stale_workers"] == ["x"] and data["max_peak_mb"] == 513


def test_requests_do_not_change_files(client, fixture_saves):
    before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in fixture_saves.rglob("*") if p.is_file()}
    client.get("/")
    client.get("/run/x")
    client.get("/logs/x")
    client.get("/healthz")
    client.get("/api/run/x/daily")
    after = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in fixture_saves.rglob("*") if p.is_file()}
    assert before == after


def test_reads_during_wal_writes(client, fixture_saves):
    db = fixture_saves / "runs" / "x.sqlite"
    writer = sqlite3.connect(db)
    writer.execute("PRAGMA journal_mode=WAL")
    for i in range(100):
        writer.execute(
            "INSERT OR REPLACE INTO daily(date,equity) VALUES (?,?)",
            (f"2025-{i // 28 + 1:02d}-{i % 28 + 1:02d}", i),
        )
        assert client.get("/run/x").status_code == 200
    writer.commit()
    writer.close()


def test_daily_api_and_missing_run(client):
    assert len(client.get("/api/run/x/daily?every=2").json()) == 1
    assert client.get("/run/missing").status_code == 404
