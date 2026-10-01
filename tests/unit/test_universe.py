import pandas as pd

from q6.data.universe import collect_month, members_asof


def test_members_asof_does_not_use_future_month_end():
    data = pd.DataFrame(
        {
            "month_end": pd.to_datetime(["2024-01-31", "2024-02-29"]),
            "code": ["sh.600001", "sh.600002"],
            "index": ["hs300", "hs300"],
            "update_date": pd.to_datetime(["2024-01-31", "2024-02-29"]),
        }
    )
    assert members_asof(data, "2024-02-15") == ["sh.600001"]
    assert members_asof(data, "2024-02-29") == ["sh.600002"]


def test_collect_two_recorded_member_responses():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "fixtures/baostock/recorded"

    class RecordedClient:
        def index_members(self, index, date):
            path = root / f"{index}_{date}.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            return pd.DataFrame(payload["rows"], columns=payload["columns"]).astype("string")

    january = collect_month(RecordedClient(), "2024-01-31")
    february = collect_month(RecordedClient(), "2024-02-29")
    assert len(january[january["index"] == "hs300"]) == 300
    assert len(january[january["index"] == "zz500"]) == 500
    assert len(february[february["index"] == "hs300"]) == 300
    assert len(february[february["index"] == "zz500"]) == 500
    assert set(january["update_date"].dt.strftime("%Y-%m-%d")) == {"2024-01-29"}
    assert set(february["update_date"].dt.strftime("%Y-%m-%d")) == {"2024-02-26"}
