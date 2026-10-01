from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from q6.data import ingest
from q6.data.ingest import _atomic_json, fetch_daily_many
from q6.data.sources import baostock
from q6.data.sources.baostock import BaostockClient, BaostockError, to_typed

FIXTURES = Path(__file__).parents[1] / "fixtures/baostock/recorded"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_to_typed_uses_recorded_response() -> None:
    recorded = fixture("daily_sh.600000_2024-01.json")
    raw = pd.DataFrame(recorded["rows"], columns=recorded["fields"], dtype="string")
    raw.loc[0, "open"] = ""
    raw.loc[0, "volume"] = ""
    typed = to_typed(raw)
    assert typed.loc[0, "date"] == pd.Timestamp("2024-01-02")
    assert typed.loc[0, "code"] == "sh.600000"
    assert pd.isna(typed.loc[0, "open"])
    assert pd.isna(typed.loc[0, "volume"])
    assert str(typed["open"].dtype) == "float64"
    assert str(typed["volume"].dtype) == "Int64"
    assert str(typed["tradestatus"].dtype) == "int8"


def test_query_error_retry_relogin_once(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = fixture("failure_invalid_code.json")
    daily = fixture("daily_sh.600000_2024-01.json")
    calls = 0
    logins = 0

    class Result:
        def __init__(self, payload: dict):
            self.error_code = payload["error_code"]
            self.error_msg = payload["error_msg"]
            self.fields = payload["fields"]
            self.rows = iter(payload["rows"])

        def next(self) -> bool:
            try:
                self.row = next(self.rows)
                return True
            except StopIteration:
                return False

        def get_row_data(self) -> list[str]:
            return self.row

    def query(**_: object) -> Result:
        nonlocal calls
        calls += 1
        return Result(failure if calls <= 2 else daily)

    class LoginResult:
        error_code = "0"
        error_msg = ""

    def login() -> LoginResult:
        nonlocal logins
        logins += 1
        return LoginResult()

    monkeypatch.setattr(baostock.bs, "fake_query", query, raising=False)
    monkeypatch.setattr(baostock.bs, "login", login)
    monkeypatch.setattr(baostock.bs, "logout", lambda: None)
    monkeypatch.setattr(baostock.time, "sleep", lambda _: None)
    with BaostockClient(min_interval=0) as client:
        result = client.query("fake_query")
    assert calls == 3
    assert logins == 2
    assert len(result) == len(daily["rows"])


def test_resume_fetches_only_missing_tail(tmp_path: Path) -> None:
    raw = fixture("daily_sh.600000_2024-01.json")
    rows = raw["rows"]
    first_half = pd.DataFrame(rows[:11], columns=raw["fields"], dtype="string")
    destination = tmp_path / "daily/sh.600000.parquet"
    destination.parent.mkdir(parents=True)
    to_typed(first_half).to_parquet(destination, index=False)
    calls: list[tuple[str, str]] = []

    class Client:
        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def daily(self, code: str, start: str, end: str) -> pd.DataFrame:
            calls.append((start, end))
            return pd.DataFrame(rows[11:], columns=raw["fields"], dtype="string")

    result = fetch_daily_many(["sh.600000"], "2024-01-01", "2024-01-31", tmp_path, Client)
    saved = pd.read_parquet(destination)
    assert not result
    expected_start = (pd.Timestamp(rows[10][0]) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    assert calls == [(expected_start, "2024-01-31")]
    assert len(saved) == len(rows)
    assert saved["date"].is_unique


def test_atomic_json_preserves_previous_file_on_replace_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "_progress.json"
    target.write_text('{"old": true}\n')

    def fail_replace(_: str, __: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(ingest.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        _atomic_json(target, {"new": True})
    assert target.read_text() == '{"old": true}\n'
    assert list(tmp_path.iterdir()) == [target]


def test_error_fixture_is_an_actual_recording() -> None:
    recorded = fixture("failure_invalid_code.json")
    with pytest.raises(BaostockError, match=recorded["error_code"]):
        raise BaostockError(recorded["error_code"], recorded["error_msg"])
