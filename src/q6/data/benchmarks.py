"""基准指数价格日线加载器；这些价格指数不含分红，会低估基准收益。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from q6.data.lockbox import REPO_ROOT
from q6.data.snapshot import SnapshotIntegrityError, _drop_lockbox


def load_benchmarks(root: str | Path = REPO_ROOT / "data/benchmarks") -> pd.DataFrame:
    """校验清单 SHA256 后读取基准；锁定状态下在 Arrow 层过滤锁箱期。"""
    root = Path(root)
    manifest_path = root / "manifest.json"
    parquet_path = root / "index_daily.parquet"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = manifest["sha256"]
        digest = hashlib.sha256(parquet_path.read_bytes()).hexdigest()
        if digest != expected:
            raise SnapshotIntegrityError(
                f"基准文件 SHA256 不匹配：期望 {expected}，实际 {digest}"
            )
        table = pq.read_table(parquet_path)
    except SnapshotIntegrityError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SnapshotIntegrityError(f"无法读取基准清单/数据 {root}: {exc}") from exc
    return _drop_lockbox(table, "daily").to_pandas()
