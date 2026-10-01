# P1-05 数据源能力实测报告

实测时间：2026-10-01（UTC+8）；Python 3.12；akshare 版本 1.19.1；baostock 版本由 `uv` 临时环境解析。探测脚本：[datasource_probe.py](../../scripts/probe/datasource_probe.py)、[event_probe.py](../../scripts/probe/event_probe.py)。原始输出：[baostock.txt](probe_raw/baostock.txt)、[baostock-event.txt](probe_raw/baostock-event.txt)、[akshare-direct.txt](probe_raw/akshare-direct.txt)、[akshare-proxy-partial.txt](probe_raw/akshare-proxy-partial.txt)。Akshare 分别尝试了清除代理的直连和 `HTTPS_PROXY/HTTP_PROXY=http://127.0.0.1:7891`；代理下日线成功，但分钟线请求失败；代理全量探测在股票列表接口停滞，故该次日志为部分输出。Akshare 直连 50 次日线请求已完成；代理环境下的 50 次连续请求超过 45 秒仍未返回，停止该次等待，代理速率结果未取得。完整字段结果以直连输出及成功的代理日线输出为准。

## 1. 日线 OHLCV（不复权）

**调用**：baostock `query_history_k_data_plus("sh.600000", fields, "2024-01-01", "2024-12-31", frequency="d", adjustflag="3")`；akshare `stock_zh_a_hist(symbol="600000", period="daily", start_date="20240101", end_date="20241231", adjust="")`。

**实测输出（原样）**

```text
baostock rows=242 columns=['date', 'code', 'open', 'high', 'low', 'close', 'preclose', 'volume', 'amount', 'adjustflag', 'turn', 'tradestatus', 'pctChg', 'isST']
2024-01-02 sh.600000 6.6300 6.6500 6.6000 6.6000 6.6200 22066700 146066303.7200 3 0.075200 1 -0.302100 0
2024-01-03 sh.600000 6.5900 6.6500 6.5900 6.6400 6.6000 18203654 120639706.0100 3 0.062000 1 0.606100 0
        日期   股票代码   开盘   收盘   最高   最低    成交量         成交额   振幅   涨跌幅   涨跌额  换手率
2024-01-02 600000 6.63 6.60 6.65 6.60 220667 146066304.0 0.76 -0.30 -0.02 0.08
2024-01-03 600000 6.59 6.64 6.65 6.59 182037 120639706.0 0.91  0.61  0.04 0.06
```

**日期/字段**：两家本次都返回 242 行，日期为 2024-01-02 至 2024-12-31。Baostock 返回 `date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,isST`，akshare 返回列名 `日期,股票代码,开盘,收盘,最高,最低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率`。Akshare 的成交量单位与 baostock 不同（样例 220667 对 22066700），接入前需归一化。最早可用日期本次未通过历史全量日线扫描确定。

**限速观察**：50 次请求见第 9 项。**结论：baostock 可用；akshare 可用（本轮在配置代理后请求成功，直连失败）。**

## 2. 复权因子

**调用**：baostock `query_adjust_factor("sh.600519", "2020-01-01", "2025-12-31")`；akshare 用 `stock_zh_a_daily(symbol="sh600519", start_date="20200101", end_date="20261231", adjust="qfq-factor")` 探测。

**实测输出（原样）**

```text
baostock columns=['code', 'dividOperateDate', 'foreAdjustFactor', 'backAdjustFactor', 'adjustFactor']
sh.600519 2020-06-24 0.856267 6.566931 6.566931
sh.600519 2021-06-25 0.864329 6.628762 6.628762
akshare stock_zh_a_daily: rows=33 columns=['date', 'qfq_factor']
2026-06-26 1.0000000000000000
2025-12-19 1.0236675985693000
```

**日期/字段**：baostock 提供操作日、前/后复权因子和因子；Akshare 找到 qfq-factor 接口，返回 `date, qfq_factor`，本次观测日期覆盖 2020 至 2026（代码及原始范围见 akshare 原始文件）。

**限速观察**：未单独压测该接口。Akshare 因子请求成功。**结论：baostock 可用；akshare 部分可用（返回 qfq 因子形式，需统一因子定义后再接入）。**

## 3. 5 分钟线最早日期

**调用**：baostock `query_history_k_data_plus(..., frequency="5", adjustflag="3")`，按 1990–2019 分段、之后逐年查询；akshare `stock_zh_a_hist_min_em(symbol="600000", period="5", adjust="")`。

**实测输出（原样）**

```text
### 5m_window_2019-2019
rows=0 columns=[]
### 5m_window_2020-2020
rows=11664 columns=['date', 'time', 'code', 'open', 'high', 'low', 'close', 'volume', 'amount']
date_range=2020-01-02..2020-12-31
2020-01-02 20200102093500000 sh.600000 12.4700 12.5800 12.4700 12.5300 7474761 93513468.0000
akshare ERROR ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))
```

**日期/字段**：本次 baostock 最早取到 2020-01-02，2020 年分段的字段为 `date,time,code,open,high,low,close,volume,amount`；2025–2026 分段也有数据。Akshare 日期边界无法确定。

**限速观察**：宽年份区间可能等待较久，脚本改为逐年分段避免全区间查询卡住。**结论：baostock 可用（观测区间从 2020-01-02 起）；akshare 当前不可用（直连和代理均失败，日期未确认）。**

## 4. 沪深300 / 中证500历史成分

**调用**：baostock `query_hs300_stocks(date)`、`query_zz500_stocks(date)`，日期 2008-01-31、2015-06-30、2020-12-31；Akshare `index_stock_cons_csindex(symbol="000300"/"000905")`。

**实测输出（原样）**

```text
baostock hs300_2008-01-31 rows=300; updateDate=2008-01-28
baostock zz500_2008-01-31 rows=500; updateDate=2008-01-28
baostock hs300_2015-06-30 rows=300; updateDate=2015-06-29
baostock zz500_2015-06-30 rows=500; updateDate=2015-06-29
baostock hs300_2020-12-31 rows=300; updateDate=2020-12-28
baostock zz500_2020-12-31 rows=500; updateDate=2020-12-28
akshare signature=(symbol: str = '000300') -> pandas.DataFrame
2026-09-30 000300 沪深300 CSI 300 000001 平安银行
2026-09-30 000905 中证500 CSI 500 000009 中国宝安
```

**日期/字段**：baostock 三个日期均返回指定指数标准数量，字段为 `updateDate,code,code_name`；日期落在请求日期之前的最近更新日。Akshare 成分接口签名没有日期参数，本次仅取到当前（2026-09-30）成分，字段见原始文件，未找到该接口的历史时点能力。最早历史日期：baostock 本任务最早测试日 2008-01-31 返回成功；Akshare 未测到历史边界。

**限速观察**：未单独压测。**结论：baostock 可用；akshare 部分可用（仅当前成分，不能满足 PIT 历史成分）。**

## 5. 股票列表与退市股

**调用**：baostock `query_all_stock("2010-06-30")` 并检查 `sh.600001`、`sz.000527`；akshare `stock_info_a_code_name()` 并检查 `600001`、`000527`。

**实测输出（原样）**

```text
baostock rows=2184 columns=['code', 'tradeStatus', 'code_name']
contains_sh.600001=False
contains_sz.000527=True
akshare rows=5572 columns=['code', 'name']
contains_600001=False
contains_000527=False
```

**字段/日期**：baostock 返回的是 2010-06-30 当日股票/证券快照，不是当前全量历史证券库；邯郸钢铁当日已不在快照，美的电器代码当日仍在。Akshare 返回当前代码和名称，两个样例代码均不在当前表。列表均没有历史退市日期字段。

**限速观察**：Akshare 当前列表请求成功。**结论：两者部分可用；baostock 支持指定日期快照，akshare 支持当前列表。不能将任一接口当成含完整退市历史的证券主表。**

## 6. 行业分类的历史时点值

**调用**：baostock `query_stock_industry("sh.600519", date)`，分别查询 2015-06-30、2020-12-31；akshare `stock_individual_info_em(symbol="600519")`。

**实测输出（原样）**

```text
2015-06-29 sh.600519 贵州茅台 C15酒、饮料和精制茶制造业 证监会行业分类
2020-12-28 sh.600519 贵州茅台 C15酒、饮料和精制茶制造业 证监会行业分类
akshare ERROR ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))
```

**字段/日期**：baostock 返回 `updateDate,code,code_name,industry,industryClassification`，两次历史查询都成功；该股票行业值相同，不代表所有股票历史行业均稳定。Akshare 当前行业查询网络失败，且所选接口不接受日期参数。

**限速观察**：未单独压测。**结论：baostock 可用（可按历史日期查询）；akshare 部分可用（本次无法观察返回值，所选接口只支持当前个股信息）。**

## 7. 交易日历

**调用**：baostock `query_trade_dates("1990-01-01", "2026-10-01")`；akshare `tool_trade_date_hist_sina()`。

**实测输出（原样）**

```text
baostock rows=13071 columns=['calendar_date', 'is_trading_day']
2020-01-24 0
2020-01-25 0
2020-02-02 0
akshare rows=8797 columns=['trade_date']
holiday_window=Empty DataFrame
1990-12-19
```

**日期/字段**：baostock 从 1990-12-19 开始，字段明确包含是否交易；2020-01-24 至 2020-02-02 每一天均为非交易日（`0`）。Akshare 返回的为交易日集合，该日期窗口为空，故相同区间内没有交易日；从 1990-12-19 起有记录。

**限速观察**：两接口单次查询均成功。**结论：两者可用；baostock 字段更适合直接构造完整日历。**

## 8. 指数日线最早日期

**调用**：baostock 对 `sh.000300`、`sh.000906` 调 `query_history_k_data_plus(..., frequency="d", adjustflag="3")`；akshare 对应 `stock_zh_index_daily(symbol="sh000300"/"sh000906")`。

**实测输出（原样）**

```text
baostock sh.000300 date_range=2005-01-04..2026-09-30
baostock sh.000906 date_range=2007-01-15..2026-09-30
akshare sh000300 date_range=2002-01-04..2026-09-30
akshare sh000906 date_range=2005-01-04..2026-09-30
```

**字段**：baostock 为 `date,code,open,high,low,close,volume,amount`；Akshare 为 `date,open,high,low,close,volume`。这里的“最早”是接口此次全量返回数据的首行日期。

**限速观察**：两家接口本次均成功。**结论：两者可用；日期边界因指数和数据源不同。**

## 9. 连续 50 次日线请求

**调用**：四只样例股票轮询，单次取 2023 年日线；每个数据源连续请求 50 次，不插入人为 sleep。

**实测输出（原样）**

```text
baostock requests=50 elapsed_seconds=17.814 failures=0 failure_samples=[]
requests=50 elapsed_seconds=2.650 failures=50 failure_samples=[(1, 'ConnectionError', "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"), (2, 'ConnectionError', "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"), (3, 'ConnectionError', "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"), (4, 'ConnectionError', "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"), (5, 'ConnectionError', "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))")]
```

**字段/日期**：请求字段如第 1 项，日期 2023-01-01 至 2023-12-31。Akshare 直连 50 次失败是网络断连，不能据此判定服务端实施了限流；未观察到 HTTP 限流码。代理模式下单次日线调用成功，但连续 50 次在 45 秒内未返回结果。

**结论**：baostock 在本轮 50 次请求中零失败，约 2.81 请求/秒；Akshare 本机直连 50 次均失败（2.650 秒），所以这轮无法评价直连限速。代理模式下日线单次成功，连续请求压测未完成。

## 除权事件核验：贵州茅台 2020-06-24

**调用**：运行 `scripts/probe/event_probe.py`，查询 `sh.600519` 的不复权日线和 `query_adjust_factor`。除权因子操作日为 2020-06-24；端午假期后的下一个交易日为 2020-06-29。

```text
sh.600519 2020-06-24 foreAdjustFactor=0.856267 backAdjustFactor=6.566931 adjustFactor=6.566931
2020-06-23 sh.600519 open=1435.0000 high=1482.0000 low=1433.5200 close=1474.5000 preclose=1439.0000 adjustflag=3
2020-06-24 sh.600519 open=1463.6000 high=1465.7100 low=1445.0000 close=1460.0100 preclose=1457.4800 adjustflag=3
2020-06-29 sh.600519 open=1448.0000 high=1466.0000 low=1437.0100 close=1463.1700 preclose=1460.0100 adjustflag=3
```

不复权日线与因子原始行见 `probe_raw/baostock-event.txt`。

## 对 v6 的建议

- **主源**：baostock，日线字段完整、复权因子明确；本机实测 50 次连续日线请求零失败。历史成分、历史行业和交易日历也可按日期取值。
- **备源/补充源**：akshare 可补充通过代理验证的 A 股日线，以及 qfq 因子、交易日集合、当前股票列表、当前指数成分和指数日线；直连日线失败，Eastmoney 分钟线在直连与代理实测中均失败。建议行情回退优先验证代理可用性。
- **5 分钟线**：baostock 本次首条可用日期为 2020-01-02（2019 及更早分段为空）；akshare 最早日期未能实测。初版管道可将 baostock 的 2020-01-02 作为已验证下界，早于此日不得假定有数据。
