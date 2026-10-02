"""模拟系统（P3）的目录布局与 SQLite 表结构——supervisor / worker / janitor / web 共用的唯一约定。

    <home>/                         Q6_SIM_HOME，Win 上是 D:\\quant6
      saves/                        模拟产出，janitor 管 ≤2GB
        runs/<run_id>.sqlite        每个回放任务一个库（WAL）；检查点只有一行，原地覆盖
        archive/<run_id>.sqlite.zst 已完成任务的滚动归档（pyarrow zstd 流）
        hb/<run_id>.json            worker 心跳（原子替换写）
        logs/*.log                  RotatingFileHandler 20MB × 5，计入 2GB
        supervisor.json             supervisor 状态（原子替换写，web 只读它）
        supervisor.lock             单实例锁

活跃判定（janitor 不许动）：supervisor.json 里 workers[].run_id 列出的任务，以及 meta.status != 'done' 的库。
web 面板只用 `file:<path>?mode=ro` 打开 SQLite，从不写。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

LOG_MAX_BYTES = 20 * 1024 * 1024
LOG_BACKUPS = 5
SAVES_BUDGET = 2 * 1024**3
SAVES_ARCHIVE_AT = int(1.8 * 1024**3)

STATUS_RUNNING, STATUS_DONE, STATUS_FAILED = "running", "done", "failed"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS checkpoint (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    day TEXT NOT NULL,          -- 最后一个已完整处理的交易日 YYYY-MM-DD
    n_days INTEGER NOT NULL,    -- 已处理交易日数
    blob BLOB NOT NULL,         -- 引擎 + 策略状态（pickle）
    written_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily (
    date TEXT PRIMARY KEY, equity REAL, cash REAL, market_value REAL, n_pos INTEGER,
    buy_value REAL, sell_value REAL, fees REAL, cost_slip_imp REAL
);
CREATE TABLE IF NOT EXISTS fills (
    seq INTEGER PRIMARY KEY, ts TEXT, order_id TEXT, symbol TEXT, side TEXT, qty INTEGER, price REAL,
    commission REAL, stamp_tax REAL, transfer_fee REAL, other_fees REAL, slippage_cost REAL, impact_cost REAL
);
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY, qty INTEGER, sellable_qty INTEGER, avg_cost REAL, last_price REAL
);
CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, msg TEXT);
"""


def home() -> Path:
    return Path(os.environ.get("Q6_SIM_HOME", "sim_home")).resolve()


def saves(root: Path | None = None) -> Path:
    return (root or home()) / "saves"


def runs_dir(s: Path) -> Path:
    return s / "runs"


def archive_dir(s: Path) -> Path:
    return s / "archive"


def hb_dir(s: Path) -> Path:
    return s / "hb"


def logs_dir(s: Path) -> Path:
    return s / "logs"


def run_db(s: Path, run_id: str) -> Path:
    return runs_dir(s) / f"{run_id}.sqlite"


def ensure(s: Path) -> Path:
    for d in (runs_dir(s), archive_dir(s), hb_dir(s), logs_dir(s)):
        d.mkdir(parents=True, exist_ok=True)
    return s


def write_json_atomic(path: Path, obj) -> None:
    """先写临时文件再 os.replace：读方永远看不到半个文件。Win 上目标被读方短暂占用时重试。"""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, default=str), encoding="utf-8")
    for attempt in range(50):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 49:
                raise
            import time

            time.sleep(0.02)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, PermissionError):
        return default


def setup_logging(name: str, s: Path):
    """按名字建一个滚动文件日志（20MB × 5），返回 logger。重复调用不重复加 handler。"""
    import logging
    from logging.handlers import RotatingFileHandler

    log = logging.getLogger(f"q6.sim.{name}")
    if not log.handlers:
        logs_dir(s).mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(logs_dir(s) / f"{name}.log", maxBytes=LOG_MAX_BYTES,
                                backupCount=LOG_BACKUPS, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log
