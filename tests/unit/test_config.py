import subprocess
from pathlib import Path

import pytest

from q6.config import ConfigError, config_hash, load_config


def _write(tmp_path: Path, filename: str, content: str) -> Path:
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")
    return path


BASE = '''
[data]
start = 2005-01-04
end = 2024-06-28
'''


def test_hash_ignores_key_order_and_comments(tmp_path: Path) -> None:
    first = _write(tmp_path, "first.toml", BASE + '\n[costs]\nslippage_bp = 5.0 # fee\n')
    second = _write(
        tmp_path,
        "second.toml",
        "# a different comment\n[costs]\n# another comment\nslippage_bp=5.0\n[data]\n"
        "end=2024-06-28\nstart=2005-01-04\n",
    )
    assert config_hash(load_config(first)) == config_hash(load_config(second))


def test_hash_changes_when_value_changes(tmp_path: Path) -> None:
    first = _write(tmp_path, "first.toml", BASE + "\n[costs]\nslippage_bp = 5.0\n")
    second = _write(tmp_path, "second.toml", BASE + "\n[costs]\nslippage_bp = 6.0\n")
    assert config_hash(load_config(first)) != config_hash(load_config(second))


def test_missing_required_field_reports_path(tmp_path: Path) -> None:
    path = _write(tmp_path, "missing.toml", "[data]\nend = 2024-06-28\n")
    with pytest.raises(ConfigError, match="data.start"):
        load_config(path)


def test_extra_field_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "extra.toml", BASE + "\n[costs]\nfoo = 1\n")
    with pytest.raises(ConfigError, match="costs.foo"):
        load_config(path)


def test_overrides_apply_and_affect_hash(tmp_path: Path) -> None:
    path = _write(tmp_path, "base.toml", BASE)
    original = load_config(path)
    overridden = load_config(path, {"costs.slippage_bp": 10})
    assert overridden.costs.slippage_bp == 10
    assert config_hash(original) != config_hash(overridden)


def test_env_is_git_ignored() -> None:
    result = subprocess.run(
        ["git", "check-ignore", ".env"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_touches_lockbox_boundary():
    """锁箱期首日（2024-07-01）本身就在锁箱期内。"""
    from q6.config import Config

    def cfg(end):
        return Config.model_validate({"data": {"start": "2020-01-02", "end": end}})

    assert not cfg("2024-06-28").touches_lockbox
    assert cfg("2024-07-01").touches_lockbox
    assert cfg("2024-07-02").touches_lockbox
