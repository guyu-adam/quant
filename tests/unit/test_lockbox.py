"""锁箱期守卫（P2-20）：锁定状态下，任何代码路径都读不到 2024-07-01（含）之后的数据。"""

import ast
import json
import multiprocessing
import subprocess
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from q6.config import ConfigError, load_config
from q6.core.pit import Panel
from q6.data import lockbox
from q6.data.lockbox import LOCKBOX_START, LOCKBOX_TS, LockboxError
from q6.data.snapshot import load_snapshot, write_snapshot

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _locked():
    lockbox.relock()
    yield
    lockbox.relock()


# ---------- 合成快照：横跨锁箱期起点，边界两侧各有数据 ----------

DAILY_DATES = [
    "2023-03-01", "2024-06-27", "2024-06-28", "2024-07-01", "2024-07-02", "2025-03-03", "2026-01-05",
]


def _tables() -> dict[str, pd.DataFrame]:
    rows = [(d, c) for d in DAILY_DATES for c in ("sh.600000", "sz.000001")]
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime([d for d, _ in rows]),
            "code": [c for _, c in rows],
            "close": np.arange(len(rows), dtype=float) + 1.0,
        }
    )
    return {
        "daily": daily,
        "calendar": pd.DataFrame({"date": pd.to_datetime(DAILY_DATES)}),
        "quarantine": pd.DataFrame(
            {"code": ["sh.600000", "sh.600000"], "date": pd.to_datetime(["2024-06-28", "2024-07-01"]),
             "reason": ["a", "b"]}
        ),
        "universe_monthly": pd.DataFrame(
            {
                "month_end": pd.to_datetime(["2024-05-31", "2024-06-28", "2024-07-31"]),
                "code": ["sh.600000"] * 3,
                "index": ["hs300"] * 3,
                # 第二行属于锁箱期前的月份，但更新日期落在锁箱期里——这条信息也是锁箱期才发布的
                "update_date": pd.to_datetime(["2024-05-27", "2024-07-05", "2024-07-29"]),
            }
        ),
    }


@pytest.fixture
def snap(tmp_path: Path) -> tuple[Path, str]:
    sid = write_snapshot(_tables(), tmp_path, asof="2026-01-05", sources={}, code_version="t")
    return tmp_path, sid


def test_lockbox_constant_matches_config_default():
    assert LOCKBOX_START == date(2024, 7, 1)
    cfg = load_config(REPO / "config" / "default.toml")
    assert cfg.split.lockbox_start == LOCKBOX_START


def test_config_cannot_move_lockbox():
    with pytest.raises(ConfigError, match="lockbox_start"):
        load_config(REPO / "config" / "default.toml", {"split.lockbox_start": date(2030, 1, 1)})


@pytest.mark.parametrize("years", [None, (2005, 2024), (2023, 2024), (2024, 2024)])
@pytest.mark.parametrize("columns", [None, ("close",)])
def test_daily_never_returns_lockbox_rows(snap, years, columns):
    root, sid = snap
    daily = load_snapshot(root, sid, years=years, columns=columns)["daily"]
    assert daily["date"].max() == pd.Timestamp("2024-06-28")  # 边界前一天还在，不多删
    assert (daily["date"] < LOCKBOX_TS).all()


@pytest.mark.parametrize("years", [(2024, 2025), (2025, 2026), (2026, 2026)])
def test_explicit_lockbox_years_rejected(snap, years):
    root, sid = snap
    with pytest.raises(LockboxError):
        load_snapshot(root, sid, years=years)


def test_aux_tables_drop_lockbox_rows(snap):
    root, sid = snap
    t = load_snapshot(root, sid, tables=("calendar", "quarantine", "universe_monthly"))
    assert t["calendar"]["date"].max() == pd.Timestamp("2024-06-28")
    assert t["quarantine"]["date"].tolist() == [pd.Timestamp("2024-06-28")]
    assert t["universe_monthly"]["month_end"].tolist() == [pd.Timestamp("2024-05-31")]


def test_lockbox_partitions_are_not_even_opened(snap):
    """锁定时 2025 / 2026 分区连打开都不打开：把它们写坏，加载照样成功。"""
    root, sid = snap
    for year in (2025, 2026):
        (root / sid / "daily" / f"year={year}.parquet").write_bytes(b"garbage")
    daily = load_snapshot(root, sid)["daily"]
    assert daily["date"].max() == pd.Timestamp("2024-06-28")


def test_panel_rejects_lockbox_dates():
    ok = pd.to_datetime(["2024-06-27", "2024-06-28"])
    Panel(ok, ["a"], {"x": np.ones((2, 1))})
    bad = pd.to_datetime(["2024-06-28", "2024-07-01"])
    with pytest.raises(LockboxError):
        Panel(bad, ["a"], {"x": np.ones((2, 1))})
    long = pd.DataFrame({"date": bad, "code": ["a", "a"], "x": [1.0, 2.0]})
    with pytest.raises(LockboxError):
        Panel.from_long(long, ["x"])


# ---------- 解锁：只能通过登记 ----------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "config").mkdir(parents=True)
    (r / "config" / "final.toml").write_text("seed = 1\n", encoding="utf-8")
    _git(r, "init", "-q")
    _git(r, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    _git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return r


def test_unlock_requires_reason(repo):
    with pytest.raises(LockboxError):
        lockbox.unlock("  ", config_path=repo / "config" / "final.toml", repo_root=repo)
    assert not lockbox.is_unlocked()


def test_unlock_refuses_dirty_tree(repo):
    (repo / "config" / "final.toml").write_text("seed = 2\n", encoding="utf-8")
    with pytest.raises(LockboxError, match="未提交"):
        lockbox.unlock("final run", config_path=repo / "config" / "final.toml", repo_root=repo)
    assert not lockbox.is_unlocked()
    assert not (repo / "registry").exists()


def test_unlock_refuses_untracked_file(repo):
    (repo / "scratch.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(LockboxError, match="未提交"):
        lockbox.unlock("final run", config_path=repo / "config" / "final.toml", repo_root=repo)
    assert not lockbox.is_unlocked()


def test_unlock_refuses_config_outside_git(repo, tmp_path):
    outside = tmp_path / "other.toml"
    outside.write_text("seed = 1\n", encoding="utf-8")
    with pytest.raises(LockboxError):
        lockbox.unlock("final run", config_path=outside, repo_root=repo)
    assert not lockbox.is_unlocked()


def test_unlock_registers_then_allows(repo, snap):
    root, sid = snap
    rec = lockbox.unlock("P4 final run", config_path=repo / "config" / "final.toml", repo_root=repo)
    assert lockbox.is_unlocked()
    lines = (repo / "registry" / "lockbox_unlocks.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["git_sha"] == rec.git_sha and len(rec.git_sha) == 40
    assert entry["config_path"] == "config/final.toml"
    assert entry["reason"] == "P4 final run"
    assert len(entry["config_sha256"]) == 64
    daily = load_snapshot(root, sid, years=(2024, 2026))["daily"]
    assert daily["date"].max() == pd.Timestamp("2026-01-05")
    Panel(pd.to_datetime(["2024-07-01"]), ["a"], {"x": np.ones((1, 1))})
    lockbox.relock()
    with pytest.raises(LockboxError):
        load_snapshot(root, sid, years=(2024, 2026))


def _child_state(queue) -> None:
    queue.put(lockbox.is_unlocked())


def test_spawned_child_is_locked(repo):
    lockbox.unlock("P4 final run", config_path=repo / "config" / "final.toml", repo_root=repo)
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=_child_state, args=(queue,))
    proc.start()
    proc.join(60)
    assert queue.get(timeout=5) is False


# ---------- 静态扫描：能直接读数据文件的地方只有白名单 ----------

# 只有这些文件可以直接调用 parquet 读取函数；新增读取点必须走 load_snapshot，或加进这里并写明理由
ALLOWED_READERS = {
    "src/q6/data/snapshot.py": "加载层本身，锁箱期过滤在这里",
    "src/q6/data/ingest.py": "抓取断点续传：读回自己写的原始文件判断已抓到哪天，不做计算",
    "src/q6/market/calendar.py": "交易日历（只有日期）；进入引擎前会被 Panel 的日期检查兜住",
    "scripts/build_snapshot.py": "P1 快照构建（数据质量检查，见 PROGRESS P1 第 3 节第 4 条）",
    "scripts/build_snapshot_year.py": "P1 快照构建",
    "scripts/build_universe.py": "P1 成分股构建",
    "scripts/crosscheck.py": "P1 跨源比价（数据质量检查）",
}
READERS = {"read_parquet", "read_table", "ParquetFile", "ParquetDataset", "dataset", "read_feather"}


def _reader_calls(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
            if name in READERS:
                lines.append(node.lineno)
    return lines


def test_no_unlisted_data_readers():
    offenders = []
    for base in ("src", "scripts"):
        for path in sorted((REPO / base).rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            if rel in ALLOWED_READERS:
                continue
            lines = _reader_calls(path)
            if lines:
                offenders.append(f"{rel}:{lines}")
    assert not offenders, f"这些文件直接读数据文件，绕过了锁箱期守卫：{offenders}"


def test_scanner_catches_reader(tmp_path):
    sample = tmp_path / "x.py"
    sample.write_text(
        "import pandas as pd\nimport pyarrow.parquet as pq\npd.read_parquet('a')\npq.read_table('b')\n"
    )
    assert _reader_calls(sample) == [3, 4]
