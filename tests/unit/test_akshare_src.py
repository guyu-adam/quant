import json
from pathlib import Path

import pandas as pd

from q6.data.sources import akshare_src


def test_daily_maps_columns_and_converts_volume(monkeypatch) -> None:
    fixture = Path(__file__).parents[1] / "fixtures/akshare/stock_zh_a_hist_600000_20240102_20240104.json"
    recorded = json.loads(fixture.read_text(encoding="utf-8"))
    response = pd.DataFrame(recorded["data"], columns=recorded["columns"])
    calls: list[dict[str, str]] = []

    def fake_hist(**kwargs: str) -> pd.DataFrame:
        calls.append(kwargs)
        return response

    monkeypatch.setattr(akshare_src.ak, "stock_zh_a_hist", fake_hist)
    result = akshare_src.daily("sh.600000", "2024-01-02", "2024-01-04")

    assert calls == [{**recorded["request"], "timeout": 20}]
    assert result.columns.tolist() == [
        "date", "code", "open", "high", "low", "close", "volume", "amount", "turn", "pctChg"
    ]
    assert result.loc[0, "code"] == "sh.600000"
    assert result.loc[0, "volume"] == 22_066_700
    assert result.loc[0, "close"] == 6.60
    assert result.loc[0, "turn"] == 0.08
    assert result.loc[0, "pctChg"] == -0.30
