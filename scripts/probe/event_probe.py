#!/usr/bin/env python3
"""Print raw unadjusted Moutai bars around the 2020-06-24 ex-dividend date."""
import pandas as pd

if not hasattr(pd.DataFrame, "append"):
    pd.DataFrame.append = lambda self, other, **kw: pd.concat([self, other], **kw)
import baostock as bs

login = bs.login()
print(f"login error_code={login.error_code}, error_msg={login.error_msg}")
q = bs.query_history_k_data_plus(
    "sh.600519",
    "date,code,open,high,low,close,preclose,volume,amount,adjustflag",
    "2020-06-23",
    "2020-06-30",
    frequency="d",
    adjustflag="3",
)
print(f"error_code={q.error_code}, error_msg={q.error_msg}")
print(q.get_data().to_string(index=False))
f = bs.query_adjust_factor("sh.600519", "2020-06-23", "2020-06-30")
print(f"factor error_code={f.error_code}, error_msg={f.error_msg}")
print(f.get_data().to_string(index=False))
bs.logout()
