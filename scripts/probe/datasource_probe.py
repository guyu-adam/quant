#!/usr/bin/env python3
"""Empirical baostock and akshare capability probe for task P1-05."""
import argparse
import datetime as dt
import json
import time
import traceback


def emit(title, fn):
    print(f"\n### {title}", flush=True)
    try:
        value = fn()
        if value is not None:
            print(value, flush=True)
    except Exception as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc(limit=2)


def baostock_probe():
    import pandas as pd
    # baostock 0.8.x still calls the removed pandas.DataFrame.append API.
    if not hasattr(pd.DataFrame, "append"):
        pd.DataFrame.append = lambda self, other, **kw: pd.concat([self, other], **kw)
    import baostock as bs
    login = bs.login()
    print(f"login: error_code={login.error_code}, error_msg={login.error_msg}", flush=True)
    if login.error_code != "0":
        return

    def query(title, q):
        def run():
            print(f"error_code={q.error_code}, error_msg={q.error_msg}")
            if q.error_code != "0":
                return
            df = q.get_data()
            print(f"rows={len(df)} columns={list(df.columns)}")
            if "date" in df.columns and len(df): print(f"date_range={df['date'].min()}..{df['date'].max()}")
            if "time" in df.columns and len(df): print(f"time_range={df['time'].min()}..{df['time'].max()}")
            if title == "all_stock_2010-06-30":
                for code in ("sh.600001", "sz.000527"):
                    print(f"contains_{code}={code in set(df['code'])}")
            if title == "trade_dates":
                dates = df[df.calendar_date.between("2020-01-24", "2020-02-02")]
                print("holiday_window=" + dates.to_string(index=False))
            return df.head(3).to_string(index=False)
        emit(title, run)

    fields = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,isST"
    query("daily_unadjusted", bs.query_history_k_data_plus("sh.600000", fields, "2024-01-01", "2024-12-31", frequency="d", adjustflag="3"))
    query("adjust_factor", bs.query_adjust_factor("sh.600519", "2020-01-01", "2025-12-31"))
    # Keep the first availability-window query small; a 36-year five-minute range
    # can stall the upstream query service before returning any rows.
    query("5m_window_1990-1992", bs.query_history_k_data_plus("sh.600000", "date,time,code,open,high,low,close,volume,amount", "1990-01-01", "1992-12-31", frequency="5", adjustflag="3"))
    for start, end in (("1993-01-01", "2000-12-31"), ("2001-01-01", "2008-12-31"), ("2009-01-01", "2016-12-31"), ("2017-01-01", "2017-12-31"), ("2018-01-01", "2018-12-31"), ("2019-01-01", "2019-12-31"), ("2020-01-01", "2020-12-31"), ("2021-01-01", "2021-12-31"), ("2022-01-01", "2022-12-31"), ("2023-01-01", "2023-12-31"), ("2024-01-01", "2024-12-31"), ("2025-01-01", "2026-10-01")):
        query(f"5m_window_{start[:4]}-{end[:4]}", bs.query_history_k_data_plus("sh.600000", "date,time,code,open,high,low,close,volume,amount", start, end, frequency="5", adjustflag="3"))
    for date in ("2008-01-31", "2015-06-30", "2020-12-31"):
        query(f"hs300_{date}", bs.query_hs300_stocks(date))
        query(f"zz500_{date}", bs.query_zz500_stocks(date))
    query("all_stock_2010-06-30", bs.query_all_stock("2010-06-30"))
    for date in ("2015-06-30", "2020-12-31"):
        query(f"industry_{date}", bs.query_stock_industry("sh.600519", date))
    query("trade_dates", bs.query_trade_dates("1990-01-01", "2026-10-01"))
    for code in ("sh.000300", "sh.000906"):
        query(f"index_earliest_{code}", bs.query_history_k_data_plus(code, "date,code,open,high,low,close,volume,amount", "1990-01-01", "2026-10-01", frequency="d", adjustflag="3"))
    # Qianlong/Midea are probed directly in the historical universe result.
    query("600519_event_factors", bs.query_adjust_factor("sh.600519", "2020-01-01", "2026-10-01"))
    query("600519_event_daily", bs.query_history_k_data_plus("sh.600519", "date,code,open,high,low,close,preclose,volume,amount,adjustflag", "2020-06-23", "2020-06-26", frequency="d", adjustflag="3"))
    emit("rate_50_daily", lambda: rate_baostock(bs))
    bs.logout()


def rate_baostock(bs):
    codes = ["sh.600000", "sz.000001", "sh.600519", "sz.300750"]
    failures, t0 = [], time.monotonic()
    for i in range(50):
        code = codes[i % len(codes)]
        try:
            q = bs.query_history_k_data_plus(code, "date,open,high,low,close,volume", "2023-01-01", "2023-12-31", frequency="d", adjustflag="3")
            if q.error_code != "0": failures.append((i + 1, q.error_code, q.error_msg))
            else: q.get_data()
        except Exception as e: failures.append((i + 1, type(e).__name__, str(e)))
    return f"requests=50 elapsed_seconds={time.monotonic()-t0:.3f} failures={len(failures)} failure_samples={failures[:5]}"


def akshare_probe():
    import akshare as ak
    import pandas as pd
    import requests
    # Akshare's network helpers have no uniform timeout argument. Bound each
    # underlying requests call so one unreachable endpoint cannot hang the probe.
    _request = requests.sessions.Session.request
    def _bounded_request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", 10)
        return _request(self, method, url, **kwargs)
    requests.sessions.Session.request = _bounded_request
    print(f"akshare_version={getattr(ak, '__version__', 'unknown')}")

    def frame(title, call):
        def run():
            df = call()
            if df is None: return "returned None"
            print(f"rows={len(df)} columns={list(df.columns)}")
            if "date" in df.columns and len(df): print(f"date_range={df['date'].min()}..{df['date'].max()}")
            if "trade_date" in df.columns and len(df): print(f"date_range={df['trade_date'].min()}..{df['trade_date'].max()}")
            if "trade_date" in df.columns:
                print("holiday_window=" + df[df.trade_date.astype(str).between("2020-01-24", "2020-02-02")].to_string(index=False))
            if title == "stock_list":
                for code in ("600001", "000527"):
                    print(f"contains_{code}={code in set(df['code'].astype(str))}")
            return df.head(3).to_string(index=False)
        emit(title, run)
    frame("daily_unadjusted", lambda: ak.stock_zh_a_hist(symbol="600000", period="daily", start_date="20240101", end_date="20241231", adjust=""))
    # Search likely factor endpoints and record actual availability.
    def factors():
        names = [n for n in dir(ak) if "factor" in n.lower() or "daily" in n.lower() and "stock_zh_a" in n.lower()]
        print(f"candidate_apis={names}")
        for name, kwargs in (("stock_zh_a_daily", {"symbol":"sh600519", "start_date":"20200101", "end_date":"20261231", "adjust":"qfq-factor"}),):
            fn = getattr(ak, name, None)
            if fn:
                try:
                    df=fn(**kwargs); print(f"{name}: rows={len(df)} columns={list(df.columns)} date_range={df['date'].min()}..{df['date'].max()}\n{df.head(3).to_string(index=False)}")
                except Exception as e: print(f"{name} ERROR {type(e).__name__}: {e}")
    emit("adjust_factor_search", factors)
    frame("5m_history", lambda: ak.stock_zh_a_hist_min_em(symbol="600000", period="5", adjust=""))
    import inspect
    for symbol in ("000300", "000905"):
        fn=ak.index_stock_cons_csindex
        def constituents(fn=fn, symbol=symbol):
            df=fn(symbol=symbol)
            print(f"signature={inspect.signature(fn)} rows={len(df)} columns={list(df.columns)}")
            print(df.head(3).to_string(index=False))
        emit(f"constituents_csindex_{symbol}", constituents)
    frame("stock_list", lambda: ak.stock_info_a_code_name())
    frame("industry_current", lambda: ak.stock_individual_info_em(symbol="600519"))
    frame("trade_calendar", lambda: ak.tool_trade_date_hist_sina())
    for symbol in ("sh000300", "sh000906"):
        frame(f"index_earliest_{symbol}", lambda symbol=symbol: ak.stock_zh_index_daily(symbol=symbol))
    emit("rate_50_daily", lambda: rate_akshare(ak))


def rate_akshare(ak):
    import requests
    _request = requests.sessions.Session.request
    def _bounded_request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", 10)
        return _request(self, method, url, **kwargs)
    requests.sessions.Session.request = _bounded_request
    codes=["600000","000001","600519","300750"]
    failures=[]; t0=time.monotonic()
    for i in range(50):
        try:
            df=ak.stock_zh_a_hist(symbol=codes[i%4], period="daily", start_date="20230101", end_date="20231231", adjust="")
            if df is None or df.empty: failures.append((i+1,"empty"))
        except Exception as e: failures.append((i+1,type(e).__name__,str(e)[:200]))
    return f"requests=50 elapsed_seconds={time.monotonic()-t0:.3f} failures={len(failures)} failure_samples={failures[:5]}"


if __name__ == "__main__":
    p=argparse.ArgumentParser(); p.add_argument("provider", choices=("baostock","akshare","all"), default="all")
    a=p.parse_args()
    if a.provider in ("baostock","all"): emit("BAOSTOCK", baostock_probe)
    if a.provider in ("akshare","all"): emit("AKSHARE", akshare_probe)
