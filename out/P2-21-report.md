# P2-21 敏感性分析交付报告

工作区：`/Users/guyu/Projects/agents/bob/wt/P2-21`，分支 `bob/P2-21`，基点 `0d98f8a0e454d38cac471fb07b8aee568d49e875`。

## 实现与产物

- `src/q6/research/sensitivity.py`：冻结 `Variant`、配置笛卡尔积 `grid`、按策略字段分组，组内通过 `VectorEngine.run_many` 一次加载，进程池采用 spawn、workers 限制 1–3；复用相同策略参数的权重，统计 CAGR、波动、夏普、回撤、费用、滑点/冲击、单边换手、成交/退市笔数与子进程峰值 RSS。
- `scripts/run_sensitivity.py`：运行 LowVolEqualWeight / CSMultiFactor 的成本、撮合、退市网格和 LowVol 参数网格；输出二维热力图/一维柱状图、CSV、默认格排名和过拟合提示。
- `tests/unit/test_sensitivity.py`：5 项 FakeFeed 合成测试，覆盖网格落点、成本单调性、workers 结果顺序与数值、默认格对照、退市回收率影响。
- `pyproject.toml` / `uv.lock`：新增独立 `report` dependency group，matplotlib 不进入核心依赖。
- CSV（小型研究数据，纳入 git）：`docs/research/sensitivity/{lowvol,csmf}_{cost,matching,delist}.csv` 与 `docs/research/sensitivity/lowvol_params.csv`。
- CSV 和 PNG 完整运行输出在 `out/sensitivity/`；报告位于 `out/P2-21-report.md`。

## 验收记录

### 单元与属性测试

命令：`uv run pytest -q tests/unit tests/property`

真实输出（退出码 0，耗时 28.52 秒）：

```text
........................................................................ [ 19%]
........................................................................ [ 39%]
........................................................................ [ 58%]
........................................................................ [ 78%]
........................................................................ [ 97%]
.........                                                                [100%]
```

### Lookahead 测试

命令：

```sh
export Q6_SNAPSHOT_ROOT=/Users/guyu/Desktop/guyu-adam/quant/data/snapshots Q6_SNAPSHOT=6252e931a86bda15 Q6_REQUIRE_SNAPSHOT=1
for f in tests/lookahead/test_*.py; do uv run pytest -q "$f" || echo "FAIL $f"; done
```

真实完整输出（退出码 0；无 `FAIL`）：

```text
.....                                                                    [100%]
.                                                                        [100%]
.                                                                        [100%]
.                                                                        [100%]
..............................                                           [100%]
...............................                                          [100%]
......                                                                   [100%]
.                                                                        [100%]
.                                                                        [100%]
..                                                                       [100%]
..                                                                       [100%]
...........                                                              [100%]
.....                                                                    [100%]
..                                                                       [100%]
.....................                                                    [100%]
.                                                                        [100%]
...                                                                      [100%]
```

### Ruff 与 AST lookahead

命令：`uv run ruff check src tests scripts && uv run python -m q6.lint.lookahead_ast src`

真实输出（退出码 0）：

```text
All checks passed!
lookahead-ast: 0 findings
```

### 全区间真快照敏感性网格

命令：

```sh
export Q6_SNAPSHOT_ROOT=/Users/guyu/Desktop/guyu-adam/quant/data/snapshots Q6_SNAPSHOT=6252e931a86bda15 Q6_REQUIRE_SNAPSHOT=1
/usr/bin/time -l uv run --group report python scripts/run_sensitivity.py
```

区间：2006-01-01 至 2024-06-28；未缩小区间或股票池。真实完整输出：

```text
lowvol/cost: CAGR min=0.025081 median=0.070364 max=0.087462; default=0.080848, rank 6/24; max child peak_rss_mb=533.6
lowvol/matching: CAGR min=0.080147 median=0.080746 max=0.081817; default=0.080848, rank 6/12; max child peak_rss_mb=499.8
lowvol/delist: CAGR min=0.072105 median=0.076507 max=0.080848; default=0.080848, rank 1/3 **DEFAULT IS BEST / OVERFIT SIGNAL**; max child peak_rss_mb=479.0
lowvol/params: CAGR min=0.049611 median=0.073776 max=0.113808; default=0.080848, rank 4/9; max child peak_rss_mb=571.1
csmf/cost: CAGR min=0.042355 median=0.132593 max=0.168237; default=0.154731, rank 6/24; max child peak_rss_mb=907.8
csmf/matching: CAGR min=0.154631 median=0.154750 max=0.155433; default=0.154731, rank 8/12; max child peak_rss_mb=922.4
csmf/delist: CAGR min=0.148962 median=0.151561 max=0.154731; default=0.154731, rank 1/3 **DEFAULT IS BEST / OVERFIT SIGNAL**; max child peak_rss_mb=782.3
All grids peak_rss_mb max=922.4
      386.71 real       394.65 user        24.49 sys
           967204864  maximum resident set size
                   0  average shared memory size
                   0  average unshared data size
                   0  average unshared stack size
            15394520  page reclaims
                 172  page faults
                   0  swaps
                   0  block input operations
                  31  messages sent
                   1  messages received
                   1  signals received
                4164  voluntary context switches
               81400  involuntary context switches
           672148996  instructions retired
          261579273  cycles elapsed
            14443840  peak memory footprint
```

CSV 真实行数：LowVol cost 24、matching 12、delist 3、params 9；CSMultiFactor cost 24、matching 12、delist 3。默认格 CAGR 与上面脚本输出一致。

### 耗时与未通过项

最终全量网格耗时为 386.71 秒（6 分 26.71 秒）；单元/属性测试 28.66 秒；lookahead 循环约 49 秒。调试阶段曾修正绘图参数列、并在重复计算策略权重后加 segment 级复用；以上验收输出来自修正后的完整成功运行。

**未满足内存门槛：**要求每个子进程 ≤512 MiB，实际各网格峰值范围 479.0–922.4 MiB，多个网格超限；主进程 `/usr/bin/time -l` 最大驻留集 967,204,864 字节。该限制在当前未修改引擎实现下未达成，所有真实峰值如上记录，未通过缩小股票池或区间规避。
