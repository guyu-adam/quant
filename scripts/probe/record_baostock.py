"""Record small, verbatim Baostock responses for offline tests."""

from __future__ import annotations

import json
from pathlib import Path

import baostock as bs

OUT = Path(__file__).parents[2] / "tests/fixtures/baostock/recorded"


def record(name: str, result: object) -> None:
    rows = []
    while result.next():
        rows.append(result.get_row_data())
    payload = {
        "error_code": result.error_code,
        "error_msg": result.error_msg,
        "fields": list(result.fields),
        "rows": rows,
    }
    (OUT / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"Baostock login failed: {login.error_code} {login.error_msg}")
    try:
        record(
            "daily_sh.600000_2024-01",
            bs.query_history_k_data_plus(
                "sh.600000",
                "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,isST",
                "2024-01-01",
                "2024-01-31",
                frequency="d",
                adjustflag="3",
            ),
        )
        record(
            "failure_invalid_code",
            bs.query_history_k_data_plus("invalid", "date,code", "2024-01-01", "2024-01-31"),
        )
    finally:
        bs.logout()


if __name__ == "__main__":
    main()
