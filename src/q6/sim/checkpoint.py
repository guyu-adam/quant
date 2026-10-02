"""检查点（P3-02）：一个回放任务一个 SQLite 库（WAL），表结构见 layout.SCHEMA。

`save()` 在**一个事务**里做四件事：追加自上次检查点以来的日记录 / 成交、重写当前持仓表、覆盖唯一的检查点行。
所以库里的 daily / fills 永远恰好对应检查点那一天为止——进程在任何位置被杀，重启后从检查点续跑，
不会重复写也不会漏写。检查点行原地覆盖（id=1），库体积只随日记录 / 成交增长。

检查点 blob 是 pickle（EngineRun.state_dict()，含策略对象）。只读自己写的库，不接收外部文件。
"""

from __future__ import annotations

import json
import pickle
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from q6.engine.event import EngineRun
from q6.sim.layout import SCHEMA, STATUS_RUNNING

_DAILY_COLS = ("equity", "cash", "market_value", "n_pos", "buy_value", "sell_value", "fees", "cost_slip_imp")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.executescript(SCHEMA)
    return conn


def init_meta(conn: sqlite3.Connection, run_id: str, spec: dict) -> None:
    """首次创建时写 run_id / spec / status；已存在时核对 spec 未变（同一个 run_id 不许换配置续跑）。"""
    old = get_meta(conn, "spec")
    new = json.dumps(spec, sort_keys=True, ensure_ascii=False)
    if old is not None and old != new:
        raise ValueError(f"{run_id} 的库里已有不同的 spec，拒绝续跑（换配置请换 run_id）")
    if old is None:
        set_meta(conn, run_id=run_id, spec=new, status=STATUS_RUNNING, created=_now())


def set_meta(conn: sqlite3.Connection, **kv) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                         [(k, str(v)) for k, v in kv.items()])
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else row[0]


def event(conn: sqlite3.Connection, kind: str, msg: str) -> None:
    conn.execute("INSERT INTO events(ts, kind, msg) VALUES (?, ?, ?)", (_now(), kind, msg))


def save(conn: sqlite3.Connection, run: EngineRun) -> None:
    if run.last_day is None:
        return
    records, fills, delistings = run.drain()
    blob = pickle.dumps(run.state_dict(), protocol=pickle.HIGHEST_PROTOCOL)
    st = run.broker.state
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.executemany(
            f"INSERT INTO daily(date, {', '.join(_DAILY_COLS)}) VALUES ({', '.join('?' * 9)})",
            [(r["date"].strftime("%Y-%m-%d"), *(r[c] for c in _DAILY_COLS)) for r in records])
        conn.executemany(
            "INSERT INTO fills(ts, order_id, symbol, side, qty, price, commission, stamp_tax, transfer_fee,"
            " other_fees, slippage_cost, impact_cost) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(f.ts.isoformat(), f.order_id, f.symbol, f.side.name, f.qty, f.price, f.commission, f.stamp_tax,
              f.transfer_fee, f.other_fees, f.slippage_cost, f.impact_cost) for f in fills])
        conn.executemany("INSERT INTO events(ts, kind, msg) VALUES (?, 'delisting', ?)",
                         [(_now(), json.dumps(d, default=str, ensure_ascii=False)) for d in delistings])
        conn.execute("DELETE FROM positions")
        conn.executemany("INSERT INTO positions VALUES (?,?,?,?,?)",
                         [(p.symbol, p.qty, p.sellable_qty, p.avg_cost, p.last_price)
                          for p in st.positions.values()])
        conn.execute("INSERT OR REPLACE INTO checkpoint(id, day, n_days, blob, written_at)"
                     " VALUES (1,?,?,?,?)",
                     (run.last_day.strftime("%Y-%m-%d"), run.n_days, blob, _now()))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def load(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT blob FROM checkpoint WHERE id = 1").fetchone()
    return None if row is None else pickle.loads(row[0])


def read_daily(path_or_conn) -> pd.DataFrame:
    conn = path_or_conn if isinstance(path_or_conn, sqlite3.Connection) else \
        sqlite3.connect(f"file:{Path(path_or_conn).as_posix()}?mode=ro", uri=True)
    try:
        df = pd.read_sql_query("SELECT * FROM daily ORDER BY date", conn)
    finally:
        if conn is not path_or_conn:
            conn.close()
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")
