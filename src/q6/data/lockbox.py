"""锁箱期守卫（P2-20）：2024-07-01（含当天）之后的数据在 P4 最终运行前读不到。

规则：
- 锁箱期起点是本模块的常量 LOCKBOX_START，**不从配置读**。配置里的 split.lockbox_start 只能等于它
  （config.py 校验），所以改配置、改命令行覆盖项都挪不动锁箱期。
- 默认锁定。进程内唯一的解锁入口是 unlock()：工作区必须干净、配置文件必须已经 commit 且与 HEAD 一致
  （预登记），先往登记簿 registry/lockbox_unlocks.jsonl 追加一条并 fsync，成功后才解锁。
  没有环境变量开关；子进程（multiprocessing spawn）不继承解锁状态，默认仍是锁定的。
- 加载层（snapshot.load_snapshot）和面板层（core.pit.Panel）都调用这里做检查，详见各自的 docstring。
  其它直接读 parquet 的代码路径由 tests/unit/test_lockbox.py 的静态扫描限定在白名单内。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

LOCKBOX_START = date(2024, 7, 1)
LOCKBOX_TS = pd.Timestamp(LOCKBOX_START)
REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRY_NAME = "lockbox_unlocks.jsonl"


class LockboxError(PermissionError):
    """请求触碰锁箱期数据，或解锁的前置条件不满足。"""


@dataclass(frozen=True)
class UnlockRecord:
    ts: str
    git_sha: str
    config_path: str
    config_sha256: str
    reason: str


_unlocked: UnlockRecord | None = None


def is_unlocked() -> bool:
    return _unlocked is not None


def current_unlock() -> UnlockRecord | None:
    return _unlocked


def relock() -> None:
    """恢复锁定（一次锁箱期运行结束后调用；测试也用它复位）。"""
    global _unlocked
    _unlocked = None


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise LockboxError(f"git {' '.join(args)} 失败：{result.stderr.strip()}")
    return result.stdout


def unlock(
    reason: str,
    *,
    config_path: str | Path,
    repo_root: str | Path = REPO_ROOT,
    registry_dir: str | Path | None = None,
) -> UnlockRecord:
    """登记并解锁。任何前置条件不满足都抛 LockboxError，状态保持锁定。"""
    global _unlocked
    if not reason or not reason.strip():
        raise LockboxError("解锁必须写明原因")
    repo = Path(repo_root).resolve()
    dirty = _git(repo, "status", "--porcelain")
    if dirty.strip():
        raise LockboxError(f"工作区有未提交的改动，不能解锁（预登记要求代码与配置都已 commit）：\n{dirty}")
    cfg = Path(config_path).resolve()
    try:
        rel = cfg.relative_to(repo).as_posix()
    except ValueError as exc:
        raise LockboxError(f"配置文件 {cfg} 不在仓库 {repo} 内") from exc
    if not _git(repo, "ls-files", "--", rel).strip():
        raise LockboxError(f"配置文件 {rel} 没有被 git 跟踪，不能作为预登记凭证")
    sha = _git(repo, "rev-parse", "HEAD").strip()
    record = UnlockRecord(
        ts=datetime.now(UTC).isoformat(),
        git_sha=sha,
        config_path=rel,
        config_sha256=hashlib.sha256(cfg.read_bytes()).hexdigest(),
        reason=reason.strip(),
    )
    registry = Path(registry_dir) if registry_dir is not None else repo / "registry"
    registry.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {**record.__dict__, "host": platform.node(), "pid": os.getpid()}, ensure_ascii=False, sort_keys=True
    )
    with (registry / REGISTRY_NAME).open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    _unlocked = record
    return record


def add_unlock_args(parser: argparse.ArgumentParser) -> None:
    """给运行脚本加 --unlock-lockbox REASON。配置路径用脚本自己的 --config。"""
    parser.add_argument(
        "--unlock-lockbox",
        metavar="REASON",
        default=None,
        help="解锁锁箱期（只用于 P4 最终运行）；会写入 registry/lockbox_unlocks.jsonl",
    )


def apply_unlock_args(args: argparse.Namespace, config_path: str | Path) -> UnlockRecord | None:
    if getattr(args, "unlock_lockbox", None) is None:
        return None
    return unlock(args.unlock_lockbox, config_path=config_path)


def _as_ts(values) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(np.asarray(values).ravel()))


def check_dates(values, what: str) -> None:
    """锁定状态下，values 里只要有一个日期 ≥ LOCKBOX_START 就抛错。NaT 忽略。"""
    if is_unlocked():
        return
    inside = _as_ts(values)
    inside = inside[inside >= LOCKBOX_TS]
    if len(inside):
        raise LockboxError(
            f"{what} 含 {len(inside)} 个锁箱期日期（例如 {inside[0].date()} ≥ {LOCKBOX_START}）；"
            "锁箱期只在 P4 最终运行中经 unlock() 登记后才能读取"
        )


def check_year_range(years: tuple[int, int] | None, what: str) -> None:
    """显式请求的年份区间整段落在锁箱期起点所在年之后时直接拒绝（锁箱起点所在年由调用方按日期截断）。"""
    if is_unlocked() or years is None:
        return
    if max(years) > LOCKBOX_START.year:
        raise LockboxError(
            f"{what} 请求了年份 {years}，超出锁箱期起点 {LOCKBOX_START} 所在年份；"
            "锁箱期只在 P4 最终运行中经 unlock() 登记后才能读取"
        )
