import json
from pathlib import Path

import pandas as pd

from q6.data.sources import akshare_src


def test_daily_maps_sina_response_and_preserves_share_volume(monkeypatch) -> None:
    fixture = Path(__file__).parents[1] / "fixtures/akshare/stock_zh_a_daily_sh600000_20240102_20240104.json"
    recorded = json.loads(fixture.read_text(encoding="utf-8"))
    response = pd.DataFrame(recorded["data"], columns=recorded["columns"])
    calls: list[dict[str, str]] = []

    def fake_daily(**kwargs: str) -> pd.DataFrame:
        calls.append(kwargs)
        return response

    monkeypatch.setattr(akshare_src.ak, "stock_zh_a_daily", fake_daily)
    monkeypatch.setattr(akshare_src, "_call_with_route", lambda fn, kwargs, proxy: fn(**kwargs))
    result = akshare_src.daily("sh.600000", "2024-01-02", "2024-01-04")

    assert calls == [recorded["request"]]
    assert result.columns.tolist() == akshare_src._FIELDS
    assert result.loc[0, "code"] == "sh.600000"
    assert result.loc[0, "volume"] == 22_066_700
    assert result.loc[0, "close"] == 6.60
    assert result.attrs["source"] == "新浪"


def test_daily_falls_back_to_tencent_and_converts_lots(monkeypatch) -> None:
    response = pd.DataFrame({"date": ["2024-01-02"], "open": [6.63], "high": [6.65],
                             "low": [6.60], "close": [6.60], "volume": [22_066_700]})

    def fail(**kwargs: str) -> pd.DataFrame:
        raise ConnectionError("offline")

    monkeypatch.setattr(akshare_src.ak, "stock_zh_a_daily", fail)
    monkeypatch.setattr(akshare_src.ak, "stock_zh_a_hist_tx", lambda **kwargs: response)
    monkeypatch.setattr(akshare_src, "_call_with_route", lambda fn, kwargs, proxy: fn(**kwargs))
    result = akshare_src.daily("sh.600000", "2024-01-02", "2024-01-02")

    assert result.loc[0, "volume"] == 22_066_700
    assert result.attrs["source"] == "腾讯"
