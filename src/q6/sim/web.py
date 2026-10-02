"""Read-only web dashboard for Q6 simulation saves."""

from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from q6.sim import layout

VALID_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _tail(path: Path, limit: int) -> list[str]:
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            end = f.tell()
            f.seek(max(0, end - 256 * 1024))
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return data.splitlines()[-limit:]


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)  # Win 路径用正斜杠


def _rows(db: Path, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with closing(_connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, params)]


def _table(title: str, rows: list[dict], columns: list[str]) -> str:
    head = "".join(f"<th>{html.escape(c)}</th>" for c in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(row.get(c, '')))}</td>" for c in columns) + "</tr>"
        for row in rows
    )
    return f"<h2>{html.escape(title)}</h2><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _svg(values: list[float | None], label: str) -> str:
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return f"<h2>{html.escape(label)}</h2><svg viewBox='0 0 800 180' role='img'></svg>"
    if len(clean) > 2000:
        step = (len(clean) - 1) / 1999
        clean = [clean[round(i * step)] for i in range(2000)]
    low, high = min(clean), max(clean)
    span = high - low or 1
    points = " ".join(
        f"{i * 800 / max(1, len(clean) - 1):.2f},{170 - (v - low) * 150 / span:.2f}"
        for i, v in enumerate(clean)
    )
    return (
        f"<h2>{html.escape(label)}</h2>"
        "<svg viewBox='0 0 800 180' role='img' "
        f"aria-label='{html.escape(label)}'><polyline fill='none' "
        f"stroke='#2563eb' stroke-width='2' points='{points}'/></svg>"
    )


def create_app(saves: str | Path) -> FastAPI:
    s = Path(saves).resolve()
    app = FastAPI(title="Q6 simulation dashboard", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index():
        sup = layout.read_json(s / "supervisor.json", {}) or {}
        age = (
            max(0, time.time() - _timestamp(sup.get("updated_at"))) if sup.get("updated_at") else float("inf")
        )
        age_text = "unknown" if age == float("inf") else f"{age:.1f}s"
        workers = sup.get("workers", [])
        worker_rows = [{**w, "progress": f"{w.get('n_days', 0)}/{w.get('total_days', 0)}"} for w in workers]
        cols = [
            "run_id",
            "state",
            "progress",
            "day",
            "equity",
            "rss_mb",
            "peak_mb",
            "restarts",
            "hb_age_s",
        ]
        rows = "".join(
            "<tr>" + "".join(_link(r) if c == "run_id" else f"<td>{html.escape(str(r.get(c, '')))}</td>"
                             for c in cols) + "</tr>"
            for r in worker_rows
        )
        runs = _runs(s)
        size = sum(p.stat().st_size for p in s.rglob("*") if p.is_file()) if s.exists() else 0
        stale_style = "color:red" if age > 30 else ""
        restarts = sup.get("restarts_log", [])
        page = f"""<!doctype html><html><head><meta charset='utf-8'>
<meta http-equiv='refresh' content='10'><title>Q6 Sim</title>
<style>body{{font:14px sans-serif;margin:2rem}}table{{border-collapse:collapse;margin:1rem 0}}
td,th{{border:1px solid #ccc;padding:.35rem}}svg{{width:100%;max-height:220px}}</style>
</head><body><h1>Q6 Simulation</h1>
<p>pid: {sup.get("pid", "")} | started: {html.escape(str(sup.get("started_at", "")))} |
updated: {html.escape(str(sup.get("updated_at", "")))} | age: <b style='{stale_style}'>
{age_text}{" — supervisor 可能已停" if age > 30 else ""}</b></p><h2>Workers</h2>
<table><thead><tr>{"".join(f"<th>{c}</th>" for c in cols)}</tr></thead>
<tbody>{rows}</tbody></table>
<p>Queue: {html.escape(json.dumps(sup.get("queue", []), ensure_ascii=False))}</p>
<h2>全部回放任务（saves/runs）</h2><table><thead><tr><th>run_id</th><th>status</th><th>last day</th>
<th>n_days</th><th>equity</th><th>fills</th></tr></thead><tbody>{runs}</tbody></table>
<p>Done: {html.escape(json.dumps(sup.get("done", []), ensure_ascii=False))}</p>
<h2>最近重启</h2><pre>{html.escape(json.dumps(restarts[-20:], ensure_ascii=False, indent=2))}</pre>
<h2>Janitor</h2><pre>{html.escape(json.dumps(sup.get("janitor", {}), ensure_ascii=False, indent=2))}</pre>
<p>saves: {size} bytes ({size / 1024**3:.4f} GB)</p></body></html>"""
        return page

    @app.get("/run/{run_id}", response_class=HTMLResponse)
    def run_page(run_id: str):
        if not VALID_NAME.fullmatch(run_id):
            raise HTTPException(400, "invalid run_id")
        db = layout.run_db(s, run_id)
        if not db.exists():
            if (layout.archive_dir(s) / f"{run_id}.sqlite.zst").exists():
                return HTMLResponse(f"<h1>{html.escape(run_id)}：已归档</h1>")
            raise HTTPException(404, "run not found")
        meta = {r["key"]: r["value"] for r in _rows(db, "SELECT key,value FROM meta")}
        daily = _rows(db, "SELECT date,equity FROM daily ORDER BY date")
        equity = [r["equity"] for r in daily]
        drawdown = []
        peak = None
        for value in equity:
            if value is not None:
                peak = value if peak is None else max(peak, value)
                drawdown.append((value / peak - 1) * 100 if peak else 0)
            else:
                drawdown.append(None)
        positions = _rows(db, "SELECT * FROM positions ORDER BY symbol")
        fills = _rows(db, "SELECT * FROM fills ORDER BY seq DESC LIMIT 200")
        events = _rows(db, "SELECT * FROM events ORDER BY seq DESC LIMIT 200")
        logs = "\n".join(_tail(layout.logs_dir(s) / f"worker-{run_id}.log", 200))
        return f"""<!doctype html><html><head><meta charset='utf-8'>
<title>{html.escape(run_id)}</title></head><body><h1>{html.escape(run_id)}</h1>
<p>status: {html.escape(meta.get("status", ""))}</p><h2>Spec</h2>
<pre>{html.escape(json.dumps(_json(meta.get("spec")), ensure_ascii=False, indent=2))}</pre>
{_svg(equity, "净值")}{_svg(drawdown, "回撤 (%)")}
{_table("当前持仓", positions, ["symbol", "qty", "sellable_qty", "avg_cost", "last_price"])}
{_table("最近成交", fills, ["seq", "ts", "symbol", "side", "qty", "price", "commission"])}
{_table("Events", events, ["seq", "ts", "kind", "msg"])}
<h2>日志尾部</h2><pre>{html.escape(logs)}</pre></body></html>"""

    @app.get("/logs/{name}", response_class=HTMLResponse)
    def logs(name: str):
        if not VALID_NAME.fullmatch(name):
            raise HTTPException(400, "invalid log name")
        return "<pre>" + html.escape("\n".join(_tail(layout.logs_dir(s) / f"{name}.log", 500))) + "</pre>"

    @app.get("/healthz")
    def healthz():
        sup = layout.read_json(s / "supervisor.json", {}) or {}
        age = (
            max(0, time.time() - _timestamp(sup.get("updated_at"))) if sup.get("updated_at") else float("inf")
        )
        workers = sup.get("workers", [])
        stale = [w.get("run_id") for w in workers if _number(w.get("hb_age_s"), float("inf")) > 60]
        size = sum(p.stat().st_size for p in s.rglob("*") if p.is_file()) if s.exists() else 0
        peaks = [_number(w.get("peak_mb"), 0) for w in workers]
        peaks.append(_number((sup.get("job_limits") or {}).get("peak_process_mb"), 0))  # Job 的历史峰值
        max_peak = max(peaks, default=0)
        ok = age < 30 and not stale and size / 1024**3 <= 2 and max_peak <= 512
        return {
            "ok": ok,
            "supervisor_age_s": None if age == float("inf") else round(age, 3),
            "workers": len(workers),
            "stale_workers": stale,
            "saves_gb": round(size / 1024**3, 6),
            "max_peak_mb": max_peak,
        }

    @app.get("/api/run/{run_id}/daily")
    def daily_api(run_id: str, every: int = Query(default=1, ge=1)):
        if not VALID_NAME.fullmatch(run_id):
            raise HTTPException(400, "invalid run_id")
        db = layout.run_db(s, run_id)
        if not db.exists():
            raise HTTPException(404, "run not found")
        return _rows(db, "SELECT * FROM daily ORDER BY date")[::every]

    return app


def _link(row: dict) -> str:
    rid = html.escape(str(row.get("run_id", "")))
    return f"<td><a href='/run/{rid}'>{rid}</a></td>"


def _runs(s: Path) -> str:
    """saves/runs 下每个库一行（只读；库打不开的显示错误，不影响整页）。"""
    out = []
    for db in sorted(layout.runs_dir(s).glob("*.sqlite")):
        try:
            with closing(_connect(db)) as conn:
                status = conn.execute("SELECT value FROM meta WHERE key='status'").fetchone()
                last = conn.execute("SELECT date, equity FROM daily ORDER BY date DESC LIMIT 1").fetchone()
                n = conn.execute("SELECT COUNT(*) FROM daily").fetchone()[0]
                nf = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
            cells = [status[0] if status else "", last[0] if last else "", n,
                     f"{last[1]:,.0f}" if last else "", nf]
        except sqlite3.Error as e:
            cells = [f"error: {e}", "", "", "", ""]
        out.append("<tr>" + _link({"run_id": db.stem}) + "".join(f"<td>{html.escape(str(c))}</td>"
                                                                 for c in cells) + "</tr>")
    return "".join(out)


def _timestamp(value: str) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0


def _number(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _json(value):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--saves", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(create_app(args.saves), host=args.host, port=args.port, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
