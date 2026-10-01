# v6 重建进度与证据

每个阶段一节，固定写三件事：做了什么 / 证据（真实命令与输出）/ 没做到或已知局限。
逐项的复核结论记录在对应的 merge commit message 里（`git log --merges main..rewrite/v6`），这里只放总览。

---

## P1：骨架 + 数据管道 + 未来函数检测（2026-10-01，Cen 汇报，等待 Adam 验收）

### 1. 做了什么

| ID | 内容 | 谁 | 状态 | 关键提交 / 复核要点 |
|---|---|---|---|---|
| P1-01 | tag `v5-final`；切 `rewrite/v6`；清空旧目录；新 `.gitignore` | Cen | 完成 | `adfcada`；远端 `v5-final^{}` = main = `ad76ed1`，main 未动 |
| P1-02 | uv + py3.12 脚手架 | Bob | 完成 | `09e1fab`，Cen 复跑通过 |
| P1-03 | 核心类型 `core/types.py` | Cen | 完成 | `86209e4` |
| P1-04 | TOML + pydantic 配置、配置哈希、锁箱期判定 | Bob | 完成 | `487ab39` |
| P1-05 | baostock / akshare 能力实测报告 | Bob | 完成 | `bd7bab0`；`docs/research/datasource_probe.md`，Cen 复跑 2 项一致 |
| P1-06 | 交易日历 | Bob | 完成 | `8946269` |
| P1-07 | baostock 抓取器（断点续传、重试、限速、原子写） | Bob | 完成（打回 1 次） | `f86a795` |
| P1-08 | 月度中证 800 历史成分（含退市） | Bob | 完成 | `78a0dae`；Cen 用上交所公告独立核对 2008-07 调样 |
| P1-09 | akshare 备份源 + 200 点交叉校验 | Bob | 完成（打回 1 次） | `6e4780f`；`docs/research/crosscheck_report.md` |
| P1-10 | 清洗 + 由 preclose 自推复权因子 | Cen | 完成 | `bb7d685`、`327d92c`；全量对账诊断 `docs/research/adjfactor_divergence.md` |
| P1-11 | 内容寻址快照（manifest + SHA256，加载即校验） | Bob | 完成（打回 1 次） | `f57f88f` |
| P1-12 | 全量快照构建 | Bob 跑 / Cen 定 | 完成 | `f075a9b`、`d08d479`；见下方"快照" |
| P1-13 | PITView + `Panel.from_long` | Cen | 完成 | `86209e4`、`f552ea4` |
| P1-14 | 截断 / 扰动未来函数检测 | Cen | 完成 | `86209e4`、`14facfa`、`7c573a2`（RSS 修复，见第 3 节） |
| P1-15 | AST 静态扫描 + pre-commit | Bob | 完成 | `e2279ef` |
| P1-16 | 一键验证 `verify.sh` / `verify.ps1` | Bob | 完成（打回 1 次） | `f837c8c`；Win 实跑 ALL CHECKS PASSED |
| P1-16b | verify 增加进程峰值 RSS 守卫（P1-17 审查中新增） | Bob | 见第 3 节 | 分支 `bob/P1-16b` |
| P1-17 | 审查、合并、本汇报 | Cen | 完成 | 本文件 |

Bob 共交付 11 项（P1-16b 另计），其中 4 项打回过 1 次，没有一项打回 2 次。

**快照 `6252e931a86bda15`**：中证 800 历史成分并集共 2023 只，2005-01 至 2026-09，日线 842 万行，529MB。
按"放 Release 不进 git"的决定，parquet 放在 GitHub Release `snapshot-6252e931a86bda15`，git 里只放 `manifest.json`（每个文件的 SHA256 和行数）。
缺失率每年都 <0.3%，按年的统计表见 `docs/research/snapshot_stats.md`。构建时进程峰值 RSS 为 484MB。

### 2. 证据

一键验证（Mac，含快照校验，2026-10-01，提交 `7c573a2`）：

```
$ Q6_SNAPSHOT=6252e931a86bda15 /usr/bin/time -l bash scripts/verify.sh
== [1/6] uv sync --frozen            Checked 64 packages
== [2/6] ruff check                  All checks passed!
== [3/6] lookahead_ast src           lookahead-ast: 0 findings
== [4/6] pytest unit + property      64 passed in 4.03s
== [5/6] pytest lookahead            37 passed in 9.07s
== [6/6] snapshot validation         （SHA256 / 行数全部一致）
ALL CHECKS PASSED
           342622208  maximum resident set size
```

未来函数检测验收（P1-14 的 DoD）：
- 合成数据：5 种带未来函数的写法（`shift(-1)`、`center=True`、全样本 zscore、`bfill`、当日收盘价当日成交）**5/5 全部检出**；5 种干净写法 5/5 通过；换随机种子后仍然全部检出（`tests/lookahead/test_truncation_selftest.py`）。
- 真实快照（2015-05 至 2016-02，覆盖股灾、千股停牌、熔断，202 个交易日 × 1605 只）：同样 5/5 检出、5/5 通过（`tests/lookahead/test_real_snapshot.py`）。
- AST 静态扫描能检出其中 4 种。"当日收盘价当日成交"属于语义问题，靠语法检查不出来，由截断测试兜底。这符合预期，`test_ast_scan.py` 的断言就是按这个写的。
- PITView：用 hypothesis 做性质测试，在任意游标下返回数据的最大时间戳都 ≤ 游标；篡改游标之后的数据，返回结果不受影响（`tests/property/test_pit.py`）。

复权（P1-10 的 DoD）：合成用例覆盖了 10 送 10、现金分红、配股、停牌期间除权，复权后收益都连续；
真实数据回归：茅台 2015-01 至 2024-06 自推的复权因子与 baostock 的因子表、后复权价一致。

资源：项目目录总共 1.6GB（.venv 633M、data/raw 461M、快照 529M），没有超过 4GB 上限。

### 3. 没做到 / 已知局限（如实列出）

1. **进程 RSS 超限，P1-17 审查时发现并已修复。** `test_real_snapshot` 原先读两年全部列，峰值 RSS 845MB，超过 512MB 上限。
   之前没有发现，是因为 verify 脚本不检查 RSS。已在 `7c573a2` 修复：按列裁剪加收窄窗口，修复后峰值 348MB。
   另派 Bob 做 P1-16b，让 verify 每一步都检查峰值 RSS，超限即判失败。快照构建峰值 484MB，离上限只差 28MB，余量很小。P2 做全量回测时必须分块。
2. **宇宙起点。** baostock 的成分接口在 2005 年两个指数都没有返回数据，2006 年只有沪深 300，中证 500 从 2007-01 开始才有。
   所以完整的中证 800 宇宙**实际从 2007-01 起**，2005–2006 只能用作因子预热。
   另外，2021-06、2021-12、2022-12 这三次调样 baostock 的更新滞后 1–3 个月，2021-09 中证 500 只有 499 只。这些都没有人工补齐，作为已知局限记录在案。
3. **复权因子与数据商不一致的有 26 只**，另有 12 只没有数据商因子。统一按交易所 preclose 口径处理，理由见 `adjfactor_divergence.md`。
   P2 必须执行的配套规则：长期停牌后的复牌首日不参与信号计算、不允许成交。P2 回测报告里要统计这 16 个复牌日对结果的影响。
4. **锁箱期的数据在快照里，并且数据质量检查碰过它。** 快照覆盖到 2026-09，所以物理上包含锁箱期（2024-07-01 起）。
   P1 有三处读到了这段数据：全量快照统计（行数 / 缺失率）、200 点跨源比价抽样、复权因子全量对账（例如 sz.302132 2025-02-18 的因子跳变）。
   三处都只是数据质量检查，**没有计算任何收益、因子或策略指标**。
   加载层的硬拦截（P2-20）还没有做，在那之前只靠约定。P2-20 应排在所有策略研究之前完成。
5. **akshare 在本机网络下基本不可用**：直连会被断开，走代理极慢。备份源实际用的是新浪和腾讯接口，交叉校验 200/200 点收盘价零偏差。
   如果 baostock 整体停服，目前没有可以全量替代的数据源。
6. **Mac 上 lightgbm 导入失败**：本机是 Intel Mac，缺 libomp。P2 用到 LightGBM 前要解决，我没有擅自用 brew 安装。
7. **P2-01（A 股交易费用历史查证）提前做完并已合并**（`590fdf0`），是 Bob 在 P1 期间空闲时做的文档调研，没有写代码。这一项不算 P2 开工，在这里说明。
8. **Win 端验证不是最新代码。** Win 上最后一次跑 verify 是在 P1-16 合并时，之后合并的 P1-08/09/12/13/14 还没有在 Win 上重跑。P1-16b 的验收里包含 Win 重跑。
   另外 Win 上没有快照，第 6 步会显式 SKIP。
