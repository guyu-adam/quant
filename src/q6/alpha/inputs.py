"""Convert daily long-form market data into masked wide factor inputs."""

import pandas as pd


def build_inputs(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    required = {
        "date",
        "code",
        "open_hfq",
        "high_hfq",
        "low_hfq",
        "close_hfq",
        "ret",
        "amount",
        "turn",
        "volume",
        "adj_factor",
        "tradestatus",
        "bad",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"daily data missing columns: {sorted(missing)}")
    frame = df.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    if frame.duplicated(["date", "code"]).any():
        raise ValueError("daily data contains duplicate (date, code) rows")
    valid = (frame["tradestatus"] != 0) & ~frame["bad"].astype(bool)
    frame["volume_hfq"] = frame["volume"].div(frame["adj_factor"].where(frame["adj_factor"] != 0))
    frame["vwap"] = frame["amount"].div(frame["volume"].where(frame["volume"] != 0))
    frame["float_cap"] = frame["amount"].div((frame["turn"] / 100).where(frame["turn"] > 0))
    fields = {
        "open": "open_hfq",
        "high": "high_hfq",
        "low": "low_hfq",
        "close": "close_hfq",
        "ret": "ret",
        "amount": "amount",
        "turn": "turn",
        "volume": "volume_hfq",
        "vwap": "vwap",
        "float_cap": "float_cap",
    }
    result = {}
    for name, column in fields.items():
        values = frame[column].where(valid).astype("float64")
        result[name] = (
            frame.assign(_value=values).pivot(index="date", columns="code", values="_value").sort_index()
        )
        result[name].columns.name = None
    return result
