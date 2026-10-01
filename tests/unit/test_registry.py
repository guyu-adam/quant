from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path

import numpy as np
import pytest

from q6.config import load_config
from q6.registry import runs
from q6.registry.runs import RegistryTamperedError, read_runs, record_run, verify_chain

REPO = Path(__file__).resolve().parents[2]


def _worker(directory: str, worker_id: int) -> None:
    for index in range(25):
        record_run(
            kind="backtest", strategy=f"worker-{worker_id}", cfg=None, snapshot_id=None,
            params={"index": index}, metrics={}, registry_dir=directory,
        )


def _record(directory: Path, **overrides):
    args = dict(
        kind="backtest", strategy="demo", cfg=None, snapshot_id="snapshot-a",
        params={"x": 1}, metrics={"return": 0.1}, registry_dir=directory,
    )
    args.update(overrides)
    return record_run(**args)


def test_record_runs_have_required_fields_and_config_hash(tmp_path):
    cfg = load_config(REPO / "config" / "default.toml")
    first = _record(tmp_path, cfg=cfg)
    second = _record(tmp_path, cfg=cfg)
    rows = (tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 2
    assert first["git_sha"] == os.popen(f"git -C {REPO} rev-parse HEAD").read().strip()
    assert first["snapshot_id"] == "snapshot-a" and first["config_hash"]
    assert first["run_id"] and first["ts"] and first["git_dirty"] is not None
    assert first["prev_hash"] == "0" * 64
    assert second["prev_hash"] == first["hash"]
    assert verify_chain(tmp_path) == 2


@pytest.mark.parametrize("mutation", ["delete", "edit", "swap"])
def test_chain_detects_tampering(tmp_path, mutation):
    _record(tmp_path)
    _record(tmp_path, strategy="other")
    _record(tmp_path, strategy="third")
    path = tmp_path / "runs.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    if mutation == "delete":
        del lines[1]
    elif mutation == "edit":
        item = json.loads(lines[1])
        item["strategy"] = "changed"
        lines[1] = json.dumps(item, sort_keys=True, ensure_ascii=False)
    else:
        lines[0], lines[1] = lines[1], lines[0]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(RegistryTamperedError):
        verify_chain(tmp_path)


def test_multiprocess_spawn_writes_complete_chain(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    processes = [ctx.Process(target=_worker, args=(str(tmp_path), number)) for number in range(4)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(30)
        assert process.exitcode == 0
    rows = read_runs(tmp_path)
    assert len(rows) == 100
    assert len({row["run_id"] for row in rows}) == 100
    assert verify_chain(tmp_path) == 100


def test_invalid_kind_and_nonfinite_numpy_scalars(tmp_path):
    with pytest.raises(ValueError):
        _record(tmp_path, kind="bad")
    _record(tmp_path, metrics={"nan": float("nan"), "pos": float("inf"),
                               "neg": -float("inf"), "nfloat": np.float64(1.5),
                               "nint": np.int64(7)})
    metrics = read_runs(tmp_path)[0]["metrics"]
    assert metrics == {"nan": "nan", "pos": "inf", "neg": "-inf", "nfloat": 1.5, "nint": 7}


def test_lock_timeout(tmp_path, monkeypatch):
    (tmp_path / "runs.jsonl.lock").touch()
    monkeypatch.setattr(runs, "LOCK_TIMEOUT_S", 0.02)
    with pytest.raises(TimeoutError):
        _record(tmp_path)


def test_git_dirty_ignores_registry_itself(tmp_path, monkeypatch):
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "registry").mkdir()
    (tmp_path / "registry" / "runs.jsonl").write_text("a\n")
    (tmp_path / "code.py").write_text("x = 1\n")
    git("add", ".")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    monkeypatch.setattr(runs, "REPO_ROOT", tmp_path)
    (tmp_path / "registry" / "runs.jsonl").write_text("a\nb\n")  # 追加登记不算改代码
    assert runs._git_metadata()[1] is False
    (tmp_path / "code.py").write_text("x = 2\n")
    assert runs._git_metadata()[1] is True
