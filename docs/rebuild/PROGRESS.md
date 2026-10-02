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
| P1-14 | 截断 / 扰动未来函数检测 | Cen | 完成（Adam 验收打回 1 次） | `86209e4`、`14facfa`、`7c573a2`（RSS 修复，见第 3 节）；`d14bb8b`（快照传路径即报错 + 验收模式，见第 5 节） |
| P1-15 | AST 静态扫描 + pre-commit | Bob | 完成 | `e2279ef` |
| P1-16 | 一键验证 `verify.sh` / `verify.ps1` | Bob | 完成（打回 1 次） | `f837c8c`；Win 实跑 ALL CHECKS PASSED |
| P1-16b | verify 增加进程峰值 RSS 守卫（P1-17 审查中新增） | Bob 写 / Cen 收尾 | 完成 | `b54d57a`（合并 Bob）、`e59df19`、`6e3ae44`（Cen 修 2 个 Win 问题）；Mac + Win 实跑 ALL CHECKS PASSED，见第 2 节 |
| P1-17 | 审查、合并、本汇报 | Cen | 完成 | 本文件 |

Bob 共交付 12 项，其中 4 项打回过 1 次，没有一项打回 2 次。
P1-16b 的代码本身合格，但 Bob 没跑通验收：Mac 端没设 `Q6_SNAPSHOT_ROOT`，worktree 里找不到快照；Win 端被两个 bug 挡住。
我判断再派一轮的成本高于自己收尾，所以由 Cen 接手修复和验收，过程见第 3 节第 8 条。

**快照 `6252e931a86bda15`**：中证 800 历史成分并集共 2023 只，2005-01 至 2026-09，日线 842 万行，529MB。
按"放 Release 不进 git"的决定，parquet 放在 GitHub Release `snapshot-6252e931a86bda15`，git 里只放 `manifest.json`（每个文件的 SHA256 和行数）。
缺失率每年都 <0.3%，按年的统计表见 `docs/research/snapshot_stats.md`。构建时进程峰值 RSS 为 484MB。

### 2. 证据

一键验证（Mac，含快照校验，2026-10-01，提交 `6e3ae44`；第 4–6 步由 RSS 守卫统计进程树中单个进程的峰值）：

```
$ Q6_SNAPSHOT=6252e931a86bda15 bash scripts/verify.sh
== [1/6] uv sync --frozen
== [2/6] uv run ruff check src tests scripts       All checks passed!
== [3/6] uv run python -m q6.lint.lookahead_ast src
== [4/6] uv run pytest tests/unit tests/property   67 passed in 5.15s    PEAK_RSS 312.6 MiB (limit 512)
== [5/6] uv run pytest tests/lookahead             37 passed in 9.00s    PEAK_RSS 334.1 MiB (limit 512)
== [6/6] snapshot validation                       snapshot 6252e931a86bda15 OK    PEAK_RSS 103.3 MiB (limit 512)
ALL CHECKS PASSED
```

交叉核对：用 `/usr/bin/time -l uv run pytest tests/lookahead` 单独测，得 354152448 字节 = 337.7 MiB，和守卫读数（三次分别是 323.2 / 331.0 / 334.1 MiB）差在每次运行的波动范围内。

守卫反例：
- `Q6_RSS_LIMIT_MB=200 bash scripts/verify.sh`：第 4 步打印 `PEAK_RSS 312.6 MiB (limit 200)` 和 `RSS LIMIT EXCEEDED`，然后 `FAILED at step 4`。
- 单独对第 5 步跑 `rss_guard.py --limit-mb 200 -- uv run pytest tests/lookahead`：37 项全部通过，但峰值 331.0 MiB 超限，退出码为 1。

Win（`ssh win`，`git archive HEAD` 解压到 `D:\quant6\verify_check` 后 `git init`，Win 上没有快照）：

```
== [4/6] uv run pytest tests/unit tests/property   67 passed in 13.46s   PEAK_RSS 311.4 MiB (limit 512)
== [5/6] uv run pytest tests/lookahead             26 passed, 11 skipped  PEAK_RSS 104.3 MiB (limit 512)
== [6/6] snapshot validation                       SKIP snapshot (Q6_SNAPSHOT unset)  PEAK_RSS 80.6 MiB
ALL CHECKS PASSED
$env:Q6_RSS_LIMIT_MB=50 → PEAK_RSS 311.4 MiB (limit 50) / RSS LIMIT EXCEEDED / FAILED at step 4 / EXIT=1
清理后 Test-Path -LiteralPath D:\quant6\verify_check → False
```

Bob 的原始证据（未通过的那一版）在 `~/Projects/agents/bob/out/P1-16b-report.md` 和 `P1-16b-evidence/`。

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
   P1-16b 已补上守卫：verify 第 4–6 步超过 512MB 即判失败（证据见第 2 节）。快照构建峰值 484MB，离上限只差 28MB，余量很小。P2 做全量回测时必须分块。
   守卫有三点局限：
   - 第 4 步的读数（约 312MB）主要来自 `test_rss_guard` 自己故意分配的 300MB 子进程，所以第 4 步实际只能证明 ≤512，看不出 pytest 本身占多少。
   - 快照构建脚本不在 verify 里，不受守卫约束。
   - Win 端靠 50ms 轮询，极短命的进程可能漏测。
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
8. **Win 端重跑查出 2 个此前没发现的 bug，已修复。** 上一次在 Win 上跑通 verify 是 16:44（P1-16），而 P1-07 的原子写入修复在 16:45 才合入，所以之后一直没有在 Win 上测过。P1-16b 在 Win 重跑时发现：
   - `ingest._atomic_parquet` 用只读句柄做 `os.fsync`，Windows 上报 `EBADF`，2 项单测失败。Win 端抓数会因此全部失败（Mac 不受影响）。已在 `e59df19` 修复，改为 `r+b` 打开。
   - verify.ps1 第 6 步的 `python -c` 字符串里有双引号，被 PowerShell 5.1 吞掉，导致语法错误。这个隐患早就在，以前 Win 上从没设过 `Q6_SNAPSHOT`，这一步根本不执行，所以没暴露。已在 `6e3ae44` 修复，改为调用 `scripts/check_snapshot.py`。
   
   仍然存在的局限：
   - Win 上没有快照，所以真实快照的 11 项未来函数测试在 Win 上是 skip，第 6 步也是 SKIP。
   - Win 自带的 `tar.exe` 解压中文文件名 `docs/rebuild/03-Bob分工表.md` 时会报错并跳过这个文件。它只是文档，不影响验证，但说明 Win 上的 checkout 应该用 git clone，不要用 tar。

### 4. 对照 `02-任务拆分清单.md` 逐条自查 DoD（2026-10-01，HEAD 为本提交）

| ID | 清单上的 DoD | 结论 | 证据 |
|---|---|---|---|
| P1-01 | `ls-remote` 能看到 tag 和分支，main 不变 | 达成 | `git ls-remote`：`refs/heads/main` 和 `v5-final^{}` 都是 `ad76ed1`；`refs/heads/rewrite/v6` 存在 |
| P1-02 | Mac 和 Win 上 `uv sync && pytest` 都能跑通 | 达成 | 第 2 节 verify 输出，Mac 和 Win 两端 |
| P1-03 | 类型冻结 | 达成 | `tests/unit/test_types.py` |
| P1-04 | 哈希不受键顺序影响、缺字段时报错清晰、`.env` 不进 git | 达成 | `test_config.py` 中 `test_hash_ignores_key_order_and_comments`、`test_missing_required_field_reports_path`、`test_env_is_git_ignored` |
| P1-05 | 实测报告，每项附真实调用和输出 | 达成（位置有偏差） | 报告放在 `docs/research/datasource_probe.md` 和 `probe_raw/`，不在清单写的 `out/` 下 |
| P1-06 | 2015-06-15 是交易日、2020-01-24 至 01-31 休市、next/prev 正确 | 达成 | `test_calendar.py:68-70`（数据来自真实日历 fixture）、`test_navigation` |
| P1-07 | 10 只样例股、断点续传不重复抓取、mock 单测 | 达成 | `test_ingest.py` 8 项（含 resume 两项，这两项在 Win 上已按 `e59df19` 修好）；全量抓取 `data/raw/baostock` 共 4046 个 parquet（覆盖并超过 10 只样例） |
| P1-08 | `universe_monthly.parquet`；抽查 3 个时点与公开资料一致 | **部分达成** | 文件已有。3 个时点中：2008-07 和 2015-06 与上交所公告一致；2021-12 的 baostock 名单没有更新，**无法核对**（`docs/research/universe_check.md`），已列为第 3 节第 2 条局限，没有人工补录 |
| P1-09 | 偏差报告，偏差 >0.5% 的列出明细 | 达成 | `docs/research/crosscheck_report.md`（200/200 点） |
| P1-10 | 已知除权事件单测（10 送 10）收益连续；涨跌停用不复权价 | 达成 | `test_adjust_clean.py::test_ten_for_ten_bonus_continuous`（第 37 行断言原始 close 不变）；真实数据回归 `test_adjust_real.py`。涨跌停规则本身在 P2-03 实现 |
| P1-11 | 改任意一个字节都会报错；两次构建哈希一致 | 达成 | `test_snapshot.py` 中 `test_load_detects_changed_parquet_byte`、`test_write_is_content_addressed_and_parquet_bytes_are_stable` |
| P1-12 | 快照 ID、体积、行数、缺失率 | 达成 | 第 1 节"快照"、`docs/research/snapshot_stats.md` |
| P1-13 | hypothesis：返回数据的最大时间戳 ≤ 游标 | 达成 | `tests/property/test_pit.py` |
| P1-14 | 5 种未来函数全部检出，5 种干净写法全部通过 | 达成（复验修复后） | 第 2 节；合成数据和真实快照各一套。真实快照这套在 Adam 验收时报错，修复和复跑见第 5 节 |
| P1-15 | 10 个样例结果符合预期；能接进 pre-commit | 达成（hook 未启用） | `test_ast_scan.py`；hook 脚本是 `scripts/hooks/pre-commit`，但本仓库**还没有启用**，需要执行 `git config core.hooksPath scripts/hooks` |
| P1-16 | Mac 和 Win 各跑一次，有完整输出 | 达成 | 第 2 节（P1-16b 之后在两端重跑） |
| P1-17 | 汇报文件 + push | 达成 | 本文件；推送记录见汇报 |

---

## 5. P1-14 复验修复（Adam 验收打回，2026-10-01，Cen）

### 问题

Adam 按 verify 流程独立复跑，把 `Q6_SNAPSHOT` 设成快照**目录路径**，第 5 步的 11 项真实快照测试全部 ERROR：
`SnapshotIntegrityError: 快照 ID 不一致：目录=/Users/.../6252e931a86bda15，清单=6252e931a86bda15，计算=6252e931a86bda15`。

**根因**：`snapshot._read_manifest` 直接用 `Path(root) / snapshot_id` 定位目录。传绝对路径时，拼出来的目录恰好是对的（拼绝对路径会丢掉 root），但随后又拿**整条路径**去和清单里的 ID 比较，所以必然不一致。传相对路径（如 `data/snapshots/<id>`）会拼成 `data/snapshots/data/snapshots/<id>`，同样失败。

**如实说明之前的证据覆盖到哪**：第 2 节的 Mac 结果（`Q6_SNAPSHOT=6252e931a86bda15`，37 passed）是真实跑出来的，用的是**裸 ID**。本次修复前我在 `7ef2699` 上又跑了三种写法：裸 ID 11 passed，绝对路径 11 errors，相对路径 11 errors。
所以准确的说法是：真实快照截断测试**只在裸 ID 这一种写法下验证过**。文档没有写明只能传 ID，Adam 用最自然的写法就踩到了。
另外，没设快照时测试默认 skip、verify 照样打印 `ALL CHECKS PASSED`，Win 端的 P1 证据就是这种"11 skipped 还是绿的"。这个默认行为本身就是假绿的来源。

### 修复（`d14bb8b`）

| 改动 | 文件 |
|---|---|
| `Q6_SNAPSHOT` 既可以是裸 ID（在 `Q6_SNAPSHOT_ROOT` 下找），也可以是快照目录路径（绝对，或相对当前目录）。ID 一律取目录名，再和清单值、重算值三方比对 | `src/q6/data/snapshot.py` `_resolve` |
| 新增单测：绝对路径、带尾部斜杠、相对路径都能 verify/load；目录被改名时仍然报 ID 不一致。新测试在旧代码上确认会失败 | `tests/unit/test_snapshot.py` |
| **验收模式 `Q6_REQUIRE_SNAPSHOT=1`**：缺 `Q6_SNAPSHOT` 时，11 项真实快照测试 ERROR，第 6 步退出码 1 | `tests/lookahead/test_real_snapshot.py`、`scripts/check_snapshot.py` |
| 验收模式下，pytest 本次运行**出现任何 skip**都判失败（打印 `SKIP NOT ALLOWED: <nodeid>`），以后新增的 skip 也会被拦住 | `tests/conftest.py` |
| verify 结尾区分模式：`ALL CHECKS PASSED (acceptance mode: ...)`，或 `ALL CHECKS PASSED (dev mode: missing snapshot is skipped, NOT valid for acceptance; ...)` | `scripts/verify.sh`、`scripts/verify.ps1` |
| 排查中顺带发现：RSS 守卫读到峰值 0（测不到）时原先算通过，现改为 `RSS MEASUREMENT FAILED` 退出码 1，并补了单测 | `scripts/rss_guard.py`、`tests/unit/test_rss_guard.py` |

**以后验收一律这样跑**：`Q6_REQUIRE_SNAPSHOT=1 Q6_SNAPSHOT=<ID 或目录> bash scripts/verify.sh`（Win：`$env:Q6_REQUIRE_SNAPSHOT="1"`）。结尾不是 `acceptance mode` 的输出不能当作验收证据。

### 证据（原样）

Mac，验收模式，按 Adam 的写法传绝对路径，HEAD = `d14bb8b`：

```
$ Q6_REQUIRE_SNAPSHOT=1 Q6_SNAPSHOT=/Users/guyu/Desktop/guyu-adam/quant/data/snapshots/6252e931a86bda15 bash scripts/verify.sh; echo "EXIT=$?"
== [1/6] uv sync --frozen
Checked 64 packages in 13ms
TIME [1/6] uv sync --frozen: 0s
== [2/6] uv run ruff check src tests scripts
All checks passed!
TIME [2/6] uv run ruff check src tests scripts: 0s
== [3/6] uv run python -m q6.lint.lookahead_ast src
lookahead-ast: 0 findings
TIME [3/6] uv run python -m q6.lint.lookahead_ast src: 0s
== [4/6] uv run pytest tests/unit tests/property
......................................................................   [100%]
70 passed in 5.38s
PEAK_RSS 312.6 MiB (limit 512)
TIME [4/6] uv run pytest tests/unit tests/property: 6s
== [5/6] uv run pytest tests/lookahead
.....................................                                    [100%]
37 passed in 9.28s
PEAK_RSS 329.5 MiB (limit 512)
TIME [5/6] uv run pytest tests/lookahead: 10s
== [6/6] snapshot validation
snapshot /Users/guyu/Desktop/guyu-adam/quant/data/snapshots/6252e931a86bda15 OK
PEAK_RSS 104.3 MiB (limit 512)
TIME [6/6] snapshot validation: 2s
ALL CHECKS PASSED (acceptance mode: real snapshot required)
EXIT=0
```

第 5 步是 37 = 26 + 11，没有 skip（验收模式下有 skip 会直接失败），说明 11 项真实快照测试确实跑了并且通过。

另外两种写法（只跑真实快照测试，验收模式）：

```
$ Q6_REQUIRE_SNAPSHOT=1 Q6_SNAPSHOT=6252e931a86bda15 uv run pytest tests/lookahead/test_real_snapshot.py
11 passed in 8.30s
$ Q6_REQUIRE_SNAPSHOT=1 Q6_SNAPSHOT=data/snapshots/6252e931a86bda15 uv run pytest tests/lookahead/test_real_snapshot.py
11 passed in 8.34s
```

反例：Mac，验收模式，不给快照，必须失败：

```
$ env -u Q6_SNAPSHOT Q6_REQUIRE_SNAPSHOT=1 bash scripts/verify.sh; echo "EXIT=$?"
== [4/6] uv run pytest tests/unit tests/property   70 passed in 5.55s   PEAK_RSS 312.7 MiB (limit 512)
== [5/6] uv run pytest tests/lookahead
.....EEEEEEEEEEE.....................                                    [100%]
ERROR tests/lookahead/test_real_snapshot.py::test_real_shape - Failed: Q6_REQ...
ERROR tests/lookahead/test_real_snapshot.py::test_real_leaky_detected[leaky_bfill]
...（其余 9 项同样 ERROR，原因均为 "Q6_REQUIRE_SNAPSHOT=1 但 Q6_SNAPSHOT 未设置"）
26 passed, 11 errors in 1.53s
PEAK_RSS 120.5 MiB (limit 512)
FAILED at step 5
EXIT=1

$ env -u Q6_SNAPSHOT Q6_REQUIRE_SNAPSHOT=1 uv run python scripts/check_snapshot.py; echo "EXIT=$?"
FAIL snapshot: Q6_REQUIRE_SNAPSHOT=1 but Q6_SNAPSHOT unset
EXIT=1
```

反例：conftest 的 skip 拦截（临时放一个 `pytest.skip("demo")` 的测试文件，跑完删除）：验收模式下打印 `SKIP NOT ALLOWED: tests/unit/test_zz_tmp_skip.py::test_x`，退出码 1；开发模式退出码 0。

Mac，开发模式，不给快照（日常用法，结尾明确标出不能用于验收）：

```
26 passed, 11 skipped in 1.46s
SKIP snapshot (Q6_SNAPSHOT unset)
ALL CHECKS PASSED (dev mode: missing snapshot is skipped, NOT valid for acceptance; set Q6_REQUIRE_SNAPSHOT=1)
```

Win（`ssh win`，用 `git archive` 把 `d14bb8b` 打成 zip，解压到 `D:\quant6\verify_check`，跑完已删除，`Test-Path` 为 False。Win 上没有快照，所以只能验证开发模式和验收模式失败这两条路径）：

```
### dev mode
== [4/6] ...  70 passed in 13.70s   PEAK_RSS 311.4 MiB (limit 512)
== [5/6] ...  26 passed, 11 skipped in 0.80s   PEAK_RSS 105.9 MiB (limit 512)
== [6/6] ...  SKIP snapshot (Q6_SNAPSHOT unset)   PEAK_RSS 80.6 MiB (limit 512)
ALL CHECKS PASSED (dev mode: missing snapshot is skipped, NOT valid for acceptance; set Q6_REQUIRE_SNAPSHOT=1)
EXIT=0
### acceptance mode
== [5/6] ...  26 passed, 11 errors in 0.87s   PEAK_RSS 106.2 MiB (limit 512)
FAILED at step 5
EXIT=1
### check_snapshot.py alone, acceptance mode
FAIL snapshot: Q6_REQUIRE_SNAPSHOT=1 but Q6_SNAPSHOT unset
EXIT=1
```

### 同类问题排查清单（"缺数据 / 缺依赖时静默通过"）

对 `src/ tests/ scripts/` 全量搜了 `skip`、`skipif`、`importorskip`、`xfail`、`except Exception`、`except ImportError`、`exists()`、`environ.get` 以及空的 `pass` 分支，逐个看过：

| 位置 | 行为 | 结论 / 处理 |
|---|---|---|
| `tests/lookahead/test_real_snapshot.py` | 缺 `Q6_SNAPSHOT` 时 skip | **已改**：验收模式 fail（本次主修复） |
| `scripts/check_snapshot.py` | 缺 `Q6_SNAPSHOT` 时打印 SKIP、退出码 0 | **已改**：验收模式退出码 1 |
| `scripts/verify.sh` / `verify.ps1` | 有 skip 也打印 `ALL CHECKS PASSED` | **已改**：结尾标明 acceptance / dev 模式 |
| `scripts/rss_guard.py` | Win 上 psutil 全程读不到时峰值为 0，算通过 | **已改**：读数 ≤0 判失败 |
| 其他测试（unit / property / lookahead 合成数据） | 全部依赖 `tests/fixtures/` 下入库的录制数据，没有 skip、没有按文件是否存在分支 | 没问题。fixture 缺失时会直接报 FileNotFoundError |
| `tests/conftest.py`（新增） | 验收模式下任何 skip 判失败 | 以后再出现条件 skip 也会被拦住 |
| `src/q6/data/ingest.py` 抓取 `except Exception` | 单只失败写进 `_failures*.json`，记 ERROR 日志，`fetch_daily.py` 有失败时退出码 1 | 显式失败，不改 |
| `src/q6/market/calendar.py` `except Exception` | 重试 3 次后抛 RuntimeError；`logout` 的 `except: pass` 只是清理 | 不改 |
| `scripts/crosscheck.py` `except Exception` | 失败的点进报告的失败清单 | 不改 |
| `scripts/build_snapshot.py` 中 `path.exists()` 分支 | 原始文件或因子缺失时记为"数据源缺"或 `factor_gaps`，写进统计报告（即第 3 节第 3 条那 12 只） | 显式记录，不改 |
| `scripts/hooks/pre-commit` | 没有暂存的 .py 文件时退出 0 | 合理，不改 |
| lightgbm 缺 libomp | 目前没有任何测试 import lightgbm，所以不存在静默跳过；P2 引入时必须在验收模式下硬失败 | 记入 P2 |

### 仍然存在的局限

- Win 上依然没有快照，所以 Win 端只能证明"验收模式缺快照会失败"，**证明不了真实快照测试在 Win 上能通过**。要在 Win 上做完整验收，需要先把 Release 里的快照拉到 Win（529MB，在 4G 上限以内），这件事等 Adam 决定。
- 开发模式（不设 `Q6_REQUIRE_SNAPSHOT`）仍然允许 skip，这是有意保留的，方便没有快照的环境日常开发。区分靠结尾那行文字，所以验收必须看到 `acceptance mode`。
- 未开工 P2，等 Adam 复验。

---

## P2：市场规则 + 双引擎 + 策略库 + 评估（2026-10-02 全部完成，Cen 汇报，等待 Adam 验收）

阶段汇报（证据、真实输出、未完成项）见本节末尾「P2 阶段汇报」。

### 任务状态

| ID | 内容 | 谁 | 状态 | 提交 |
|---|---|---|---|---|
| P2-20a | 锁箱期硬拦截（加载层 + Panel 层，解锁必须登记），P2 准入门槛 | Cen | 完成，Adam 已认可 | `abffa03` |
| P2-01 | 费用历史查证 | Bob | 完成（P1 期间） | `590fdf0` |
| P2-02 | 费率模块 | Bob | 合并 | `8aca977` |
| P2-03 | A 股规则：板块、涨跌停价（整数分）、手数、新股无限制天数 | Cen | 完成；真实数据全量比对见下（比 DoD 的抽 50 个更严） | `edb8f92` `02789e6` |
| P2-04 | 冲击 / 滑点 | Bob | 合并 | `7d5e122` |
| P2-05 | 撮合（集合竞价 / bar 内 / 参与率 / 涨跌停排队），向量化单一实现 | Cen | 完成 | `50ae129` |
| P2-06 | 账户与模拟券商（含费成本、T+1、除权除息、盯市） | Cen | 完成 | `ee7a0c1` |
| P2-07 | 事件引擎 + 数据源 + 时钟；退市结算；真实快照全区间回测 | Cen | 完成 | `074493f` `cf490c5` |
| P2-08 | 向量化引擎（按事件引擎 5 步逐日复刻，`run_many`，`check_weights_pit`）+ 提速 32.4s→18.0s 逐位不变 | Cen | 完成 | `a91cec9` `24feb75` |
| P2-09 | 引擎一致性套件 3 策略 × 3 段行情（真快照，权益偏差实测最大 2.3e-16） | Bob | 合并 | `28efc05` |
| P2-10 | 风控（盯市日亏含浮亏、回撤熔断 + 冷却、交易前检查），接入两个引擎 | Bob 写 / Cen 接入 | 合并 + Cen 修熔断期逐日复利减仓 | `561d0ac` `2f918cc` |
| P2-11 | 组合构建（单票 / 行业 / 换手约束投影）+ HRP | Bob | 合并 | `982d2b8` |
| P2-12 | 因子算子 25 个 | Bob | 合并 | `fa3d2a6` |
| P2-13 | 因子库 30 个 + 中性化 + 慢算子向量化 | Bob | 合并 + Cen 修 3 处 | `a9ca68f` `a3b4650` |
| P2-14 | Strategy 接口冻结 + `strategy_runner`；组合器 Combo | Cen / Bob | 完成 | `074493f` `2f6755b` `08d20fb` |
| P2-15 | 策略① 时序动量 | Bob | 合并（打回 1 次：652 只全入选导致零成交，加 max_names） | `12c6abe` `1955f64` |
| P2-16 | 策略② 截面多因子 | Bob | 合并 | `46e59ab` |
| P2-17 | 策略③ 短期反转 + 距离法配对 | Bob | 合并（打回 1 次：配对状态按下标跨年错位） | `157f8d5` `f16fdc3` |
| P2-18 | 策略④ ML：特征（Bob）、标签 + Purged K-Fold（Bob 写 / Cen 修 embargo 起点）、LightGBM 排序（Cen） | Cen + Bob | 完成；真快照 walk-forward 结果见阶段汇报 | `9285a56` `7eb9e27` `9a7abd0` `6ccb9bf` |
| P2-19 | 指标 + IC | Bob | 合并 | `8922419` |
| P2-20 | walk-forward（训练 3 年 / 测试 6 个月 / 步长 6 个月，purge + embargo，研究视界强制隔离） | Cen | 完成 | `0d98f8a` |
| P2-21 | 敏感性分析（成本 / 撮合 / 退市回收率 / 参数网格，多进程） | Bob | 合并（打回 1 次：子进程 RSS 922MB） | `b2566df` |
| P2-22 | Deflated Sharpe + PBO/CSCV（对照 2012 / 2014 论文数值样例） | Bob | 合并 | `dacd12e` |
| P2-23 | 实验登记簿（哈希链 + 跨进程锁） | Bob | 合并 + Cen 修 2 处 | `8b4f80c` `83d2857` `e4579e4` |
| P2-24 | 基准指数 + 情形区间表；宇宙等权月度再平衡（含成本）+ 买入持有 | Bob | 合并 | `585f8f0` `39bf0de` |
| P2-25 | 核心路径覆盖率（market / engine / risk / core 逐文件 ≥90%） | Bob | 合并 | `deaad24` |
| P2-26 | P2 审查、调试、阶段汇报 | Cen | 本节 | — |
| （前置） | macOS 关闭 libmalloc medium 区（RSS 碎片 92%） | Cen | 完成 | `90994ba` |

P2-23 审查时 Cen 修的两处：
1. `git_dirty`：`registry/runs.jsonl` 本身入库，不排除的话第一条记录之后永远是 dirty，字段失去意义。改为 `git status --porcelain -- . ':!registry'`，加了回归测试（`test_git_dirty_ignores_registry_itself`）。
2. **Windows 专有 bug（Win 验收跑出来的，Mac 不复现）**：一个进程正在删除锁文件时，另一个进程 `O_EXCL` 打开拿到的是 `PermissionError`（delete-pending）而不是 `FileExistsError`，没被重试循环接住，子进程直接崩溃。改为两种都按"锁被占用"重试；真的没权限时 10 秒后 `TimeoutError`，不静默。

P2-23 的已知局限：哈希链能发现中间行被改或被删，**发现不了"删掉末尾几行"**——这一点靠 `runs.jsonl` 进 git，删除会出现在 diff 里。进程在持锁期间被杀会留下锁文件，之后所有写入 10 秒超时报错（显式失败，需人工删锁）。

P2-03 的已知局限：规则表只查证到 2024-07-01 之前，之后的日期 `limit_pct` 直接抛 `NotImplementedError`，P4 前补齐；
主板首日 +44%/−36%（相对发行价）约束、股改复牌首日、退市整理期不建模（引擎靠"上市未满 N 日不交易"和隔离表排除）。

另：快照里**没有**市值、行业、财务数据。P2-13 的规模因子用 `amount / (turn/100)` 近似流通市值；价值 / 质量因子无法实现，行业中性化只做了接口（合成数据单测），没有行业数据。是否补抓行业（baostock 只有当前分类，有后视偏差）待定。

### P2-03 真实数据比对（全量，2005-01 至 2024-06，锁箱期之前）

DoD 原写"抽样 50 个涨停日逐笔比对"。手里没有交易所公布的逐日涨停价，"涨停日"只能从价格反推，会把"收在最高价、差 1 分没涨停"的日子也算进来（实测 823 例全是这种，偏差全为负），所以改用两条能直接从数据检验、且比抽样更严的性质，对**全部 6,911,152 个可交易行**做：

1. **不越界**：最高价 > 算出的涨停价 1,084 行、最低价 < 跌停价 902 行（合计约 0.03%）。其中 948 行是上市 5 日内新股；2005–2006 共 860 行是股改复牌；2007 年后剩约 200 行，抽查全是重组 / 恢复上市复牌首日（+374%、+1004% 这种）和退市整理期首日（2022–2024，股价 <1 元、−50%~−87%），都在规则表声明不建模的特例里。2008–2009 有少量疑似除权前收不一致或坏 tick。**取整错误的特征"恰好越界 1 分钱"在 2015 / 2020 两年为 0（测试断言）。**
2. **取整方向**：前收 × (1±比例) 恰好落在半分上的 702,849 个交易日里，最高价恰好等于 half-up 涨停价 16,274 次（若交易所是 half-down，这个价位不可能成交）；最低价恰好等于 half-up 跌停价 11,995 次，低于它的只有 85 次（基本都在 2005–2006 股改期）。**结论：四舍五入（half-up）正确。**

扫描中发现并修复：`sz.302132`（2025 年代码变更后的创业板 302 号段）被 `board_of` 拒绝，改为 `sz.30*` 全部归创业板。

固化为 `tests/lookahead/test_rules_real.py`（2015、2020 两年，验收模式缺快照即失败）。变异测试：取整改 half-down → 4 项失败；创业板改革日改成 2020-09-24 → 2 项失败。

**对撮合（P2-05）的要求**：特例日由价格本身暴露（当日最高 / 最低越出算出的涨跌停价），撮合一律当作"不可成交日"处理。这样做是保守的（只会少成交），且这些状态在现实中都会提前公告，不构成未来信息。

**更正**：此前这里写"`load_snapshot` 读单年 7 列峰值 RSS 约 420MB"是错的。进程内逐段测：import 后 93MB，读完一年 177MB（DataFrame 本身 21MB），算完涨跌停 234MB。`/usr/bin/time` 看到的 420MB 是整个 pytest 进程（含插件和前面的测试）的峰值。

### P2-13 审查后 Cen 修的 3 处（`a3b4650`）

1. `inputs.vwap` 用的是原始股数 → 得到的是不复权价，和 `*_hfq` 价格口径不一致（卡片要求后复权股数）。目前没有因子用到 vwap，但留着会坑后面的人。改后单测期望值从 10 改为 20（1000 / 后复权股数 50），这是按规格更正，不是迁就实现。
2. `ops` 滑窗分块上限 2e7 → 2e6 元素。
3. Bob 给 `load_snapshot` 加了 `date_range` 参数。锁箱守卫本身没被绕开（年份检查和 Arrow 层剔除都还在），但 `date_range` 落进锁箱期时会**静默返回空表**，而 `years` 落进锁箱期是报错——行为不一致。改为同样抛 `LockboxError`，加 2 个测试；删掉这行检查的变异下 2 个测试都失败。

**RSS 超限与 verify 第 5 步的改动**：合并 P2-13 后验收模式第 5 步峰值 584MB，超过 512MB。查因：单看因子数据只有 202×1605×10 列 ≈ 26MB；截断测试每个切点要复制截断 / 扰动 / 挖 NaN 三份输入，加上 macOS malloc 不归还页面，同一个 pytest 进程里几个真实快照测试的峰值一路累加。Bob 的 `test_library_real` 为此在横截面上均匀抽 300 只股票（截断测试查的是时间方向的泄漏，截面算子在 300 只上同样被执行到）；全市场 1605 只单独跑仍要 688MB，**全市场版本没有做**。
处理：verify 第 5 步改成 `tests/lookahead/` 下每个文件一个进程、各自受 512MB 约束（`verify.sh` / `verify.ps1` 同步改）。这不是放宽上限：每个进程仍然 ≤512MB，测试总数 112 不变（改前一个进程 112 passed，改后 9 个文件之和 5+30+31+6+1+2+11+5+21=112）。

Mac 验收模式（`a3b4650`）：

```
== [4/6] uv run pytest tests/unit tests/property
229 passed in 19.75s
PEAK_RSS 312.6 MiB (limit 512)
== [5/6] uv run pytest tests/lookahead (one process per file)
-- tests/lookahead/test_ast_scan.py            5 passed in 0.19s    PEAK_RSS 44.7 MiB
-- tests/lookahead/test_library_real.py       30 passed in 12.58s   PEAK_RSS 466.4 MiB
-- tests/lookahead/test_library_truncation.py 31 passed in 4.01s    PEAK_RSS 127.8 MiB
-- tests/lookahead/test_lockbox_real.py        6 passed in 0.84s    PEAK_RSS 168.7 MiB
-- tests/lookahead/test_metrics_truncation.py  1 passed in 0.66s    PEAK_RSS 115.4 MiB
-- tests/lookahead/test_ops_truncation.py      2 passed in 1.00s    PEAK_RSS 118.1 MiB
-- tests/lookahead/test_real_snapshot.py      11 passed in 8.33s    PEAK_RSS 349.5 MiB
-- tests/lookahead/test_rules_real.py          5 passed in 1.58s    PEAK_RSS 325.6 MiB
-- tests/lookahead/test_truncation_selftest.py 21 passed in 1.41s   PEAK_RSS 117.6 MiB
== [6/6] snapshot validation
PEAK_RSS 102.5 MiB (limit 512)
ALL CHECKS PASSED (acceptance mode: real snapshot required)
```
（原输出每个文件的 passed 行和 PEAK_RSS 行是分开两行的，这里为了紧凑合成一行，数字未改。）

Win 验收模式（`a3b4650`，`D:\quant6\run-verify.ps1`）：A（绝对路径）`EXIT=0`、B（裸 ID）`EXIT=0`、C（不给快照）`EXIT=1`。A 的日志：第 4 步 `229 passed in 17.59s`、PEAK_RSS 311.4 MiB；第 5 步 9 个文件同样 5/30/31/6/1/2/11/5/21 passed，最高 PEAK_RSS 362.5 MiB（test_library_real）；第 6 步 `snapshot D:\quant6\repo\data\snapshots\6252e931a86bda15 OK`；`ALL CHECKS PASSED (acceptance mode: real snapshot required)`。C 在第 5 步第二个文件 test_library_real 处报 `Q6_REQUIRE_SNAPSHOT=1 但 Q6_SNAPSHOT 未设置` 并失败。

### Win 端用真实快照跑验收模式（补 P1 留下的缺口）

P1-16c 派给 Bob 时 Win 连 GitHub 超时，只下到 1 个文件；他的下载脚本还有一个逻辑错误：把 Release 的 `manifest.json` 下载到 git 跟踪的同一路径，覆盖之后再"比对"等于自己比自己。
Cen 重写脚本 `D:\quant6\fetch-snapshot.ps1`（Release 的 manifest 单独存到 `D:\quant6\manifest.release.json`，每个文件同时核对字节数和 Release API 给出的 SHA256），并加了 `.gitattributes`（`961e8cb`），让 Win checkout 出来的 manifest 不被转成 CRLF，与 Release 逐字节一致。

下载（Win 直连 GitHub，无代理）：

```
=== UPDATE REPO ===
961e8cb chore(v6): 快照 manifest 禁止换行转换，Win checkout 与 Release 逐字节一致
=== DOWNLOAD ===
ASSET_COUNT 26
calendar.parquet             size=     37940 match=True sha_match=True
daily_year.2005.parquet      size=  11281251 match=True sha_match=True
...（2006–2025 共 20 行，全部 match=True sha_match=True）
daily_year.2026.parquet      size=  24509810 match=True sha_match=True
manifest.json                size=      5156 match=True sha_match=True
quarantine.parquet           size=      1869 match=True sha_match=True
universe_monthly.parquet     size=     78680 match=True sha_match=True
DOWNLOAD_SECONDS 80.9
=== MANIFEST: git-tracked vs Release ===
release=e13d5871d974ea241a1d81c577c2c2a3bce51c1338432f2e04aa4fa29ec1bc4d
tracked=e13d5871d974ea241a1d81c577c2c2a3bce51c1338432f2e04aa4fa29ec1bc4d
identical=True
```

第一次在 `961e8cb` 上跑验收模式：**第 4 步失败**，就是上面 P2-23 的 Windows 锁 bug：

```
FAILED tests/unit/test_registry.py::test_multiprocess_spawn_writes_complete_chain
PermissionError: [Errno 13] Permission denied: 'C:\\Users\\zhang\\AppData\\Local\\Temp\\pytest-of-zhang\\pytest-28\\test_multiprocess_spawn_writes0\\runs.jsonl.lock'
1 failed, 208 passed in 14.71s
FAILED at step 4
```

修复（`e4579e4`）后 Win 仓库 `git reset --hard origin/rewrite/v6` 到 `e4579e4`，再跑三遍（`D:\quant6\run-verify.ps1`，日志 `D:\quant6\verify-{A,B,C}.log`）：

```
##### A: acceptance, Q6_SNAPSHOT = absolute dir
EXIT=0
##### B: acceptance, Q6_SNAPSHOT = bare ID
EXIT=0
##### C: acceptance, Q6_SNAPSHOT unset (must fail)
EXIT=1
```

A（快照目录绝对路径）日志摘录：

```
== [4/6] uv run pytest tests/unit tests/property
209 passed in 16.50s
PEAK_RSS 311.4 MiB (limit 512)
== [5/6] uv run pytest tests/lookahead
45 passed in 6.62s
PEAK_RSS 238.7 MiB (limit 512)
== [6/6] snapshot validation
snapshot D:\quant6\repo\data\snapshots\6252e931a86bda15 OK
PEAK_RSS 84.3 MiB (limit 512)
ALL CHECKS PASSED (acceptance mode: real snapshot required)
```

B（裸 ID）：`209 passed in 16.33s` / `45 passed in 6.59s`（PEAK_RSS 239.7 MiB）/ `snapshot 6252e931a86bda15 OK` / `ALL CHECKS PASSED (acceptance mode: real snapshot required)`。0 skipped。

C（验收模式、不给快照）：第 5 步 `28 passed, 17 errors`，17 个 error 全部是真实快照测试（test_real_snapshot 11 个 + test_lockbox_real 6 个），原因 `Q6_REQUIRE_SNAPSHOT=1 但 Q6_SNAPSHOT 未设置`，`FAILED at step 5`。

说明：日志第 1 步出现 `NativeCommandError` 字样，是 PS 5.1 在 `*>` 重定向时把 uv 写到 stderr 的 `Checked 66 packages in 1ms` 包装成了错误记录，该步 0.027s 正常结束，不是失败。
`D:\quant6` 总体积 1,134,260,033 字节（约 1.06 GiB，含 `.venv` 和快照），≤4GB。快照和仓库保留在 Win 上，P3 使用。

**结论：P1 遗留的"Win 端证明不了真数据测试"这一缺口已补上。**

### P2-07 事件引擎：样板策略真实快照全区间回测（2026-10-01，`cf490c5`）

命令（Mac，验收快照 `6252e931a86bda15`，区间止于锁箱期前）：

```
/usr/bin/time -l uv run python scripts/backtest_example.py
```

原样输出（`/usr/bin/time` 只保留 RSS 一行）：

```
strategy=example_lowvol params={'n': 30, 'lookback': 60, 'every': 20, 'gross': 0.95} snapshot=6252e931a86bda15
days=4491 2006-01-04..2024-06-28  run_seconds=32.4
final_equity=4,182,793  CAGR=8.08%  vol=19.39%  sharpe(rf=0)=0.50  maxDD=-61.64%
fills=6761  fees=521,442  slip+impact=284,003  turnover(annual, one-way)=3.84
reasons: {'LIMIT_QUEUE': 6, 'LOCKED_LIMIT': 5, 'MISSING_ROW': 80, 'NO_CASH': 80, 'NO_VOLUME': 73, 'PARTIAL': 62, 'SUSPENDED': 50}
delistings=4 (settled at last price x recovery=1.0)
  2009-06-05 sz.000515 qty=5500 last=15.29 value=84,095 cost=74,033
  2012-03-29 sh.600263 qty=5400 last=16.43 value=88,722 cost=79,229
  2012-04-20 sh.600991 qty=5200 last=17.82 value=92,664 cost=82,595
  2016-01-28 sz.000024 qty=4700 last=40.50 value=190,350 cost=156,777
year  return   maxDD
2006  +49.54%  -16.17%
2007  +221.69%  -17.33%
2008  -52.62%  -61.64%
2009  +54.81%  -14.21%
2010  -19.11%  -23.96%
2011  -12.55%  -24.92%
2012   +4.54%  -16.00%
2013   -4.02%  -18.13%
2014  +53.53%   -6.87%
2015  +35.87%  -37.48%
2016   -7.49%  -16.26%
2017   +6.43%   -5.57%
2018  -29.12%  -33.54%
2019  +13.64%  -11.00%
2020  -10.74%  -18.68%
2021   +4.51%   -6.09%
2022   -5.13%  -12.82%
2023   +5.72%   -9.81%
2024   +7.90%   -5.91%
           363077632  maximum resident set size
```

样板策略（低波 30 只等权、20 日调仓）只用来证明引擎跑通，**不是研究结论**：18.5 年 CAGR 8.08%、最大回撤 −61.6%（2008），
2010 年以后大部分年份接近零或为负；显性费用 52 万 + 滑点冲击 28 万，相对 100 万初始资金不小。没有和基准比，没有调参。

第一次跑出来的两个问题（都已修，修之前的数字也如实记在这里）：
1. **内存超限**：峰值 RSS 1,053,929,472 字节（1.05GB > 512MB）。按代码过滤只降到 958MB；换 Arrow 内存池对比
   （同一命令，结果逐位一致）：mimalloc 945MB / system 546MB / jemalloc 375MB。根因是 Arrow 默认的 mimalloc 池不把读完的
   parquet 缓冲区还给系统。运行时 `pa.set_memory_pool` 换池实测只降到 736MB，必须在 pyarrow 首次导入前设环境变量，
   所以放在 `q6/__init__.py`（非 Windows；Windows 的 pyarrow 没有 jemalloc，Win 上的内存待实测）。
2. **退市持仓永不结算**：修之前 `STALE_HOLDING=10914`——4 只被换股吸收合并的股票（攀渝钛业→攀钢钒钛、路桥建设→中国交建、
   广汽长丰→广汽集团、招商地产→招商蛇口）在快照里没有行之后一直按最后价留在持仓里，最长 15 年，资金被锁死（NO_CASH 364 次）。
   改为连续 20 个交易日没有行情行即按最后价 × `delist_recovery` 结算成现金（停牌日数据源仍有 tradestatus=0 的行，
   所以"没有行"=已摘牌）。修前修后 CAGR 8.32% → 8.08%。

已知局限：
- `delist_recovery` 默认 1.0。对换股吸收合并大致公允（换股价通常不低于停牌前价）；对破产退市**偏乐观**（摘牌后三板价值远低于最后价）。
  本次 4 笔全是吸收合并。P2-21 敏感性分析要加 recovery=0 一档。
- 现金分红按前收再投资，未扣红利税（P2-06 已记录）。
- 缺行的前 19 天仍按最后价估值。


## P2 阶段汇报（2026-10-02，Cen；代码 HEAD `f65cfe2`，等待 Adam 验收，**未开工 P3**）

### 1. 本轮（10-02 下午）做了什么

1. **P2-18d LightGBM 排序策略**（Cen，`9a7abd0` `6ccb9bf`）：lambdarank，训练与推理共用 `feature_matrix`；特征缓存读出后过
   `lockbox.check_dates`；标签在研究视界内现读收盘价，越过 train_end 的样本自然丢弃；`RankerConfig` 写死，**没有调参**。
   顺带修 `ops.ts_corr`：窗口内一侧方差只剩舍入残差时 pandas 给出 ±inf / 3.0 / −6.0，改为 NaN 并裁到 [−1, 1]（加回归测试）。
   验收模式 verify 第 4 步的 `test_no_unlisted_data_readers` 拦下了我自己脚本里两处直接读缓存 parquet——改走 `load_feature_cache`。
2. **macOS 内存根因**（Cen，`90994ba`）：`vmmap` 显示逐年读快照后 libmalloc **medium 区常驻 245MB、实际在用 20MB（碎片 92%）**，
   Python 堆（tracemalloc）稳定在 ~53MB、Arrow 池在用 8MB——不是泄漏。`malloc_zone_pressure_relief`、jemalloc decay=0、
   限制 Arrow 线程数都试过，无效。`MallocMediumZone=0`（这个大小段改走 large 路径，释放即还给系统）：

   | 进程（全区间 2006-01 至 2024-06） | 改前峰值 RSS | 改后 |
   |---|---|---|
   | 只遍历 SnapshotFeed（不跑策略） | 458MB | 303MB |
   | 低波样板，向量引擎 | 461MB | 335MB |
   | 截面多因子，向量引擎 | **808MB** | 404MB |
   | ML 特征缓存构建 | 859MB（第 3 年时，已杀掉） | 408MB |

   逐笔成交数不变，耗时不变；验收模式 verify 含一致性套件（≤1e-12）全部通过。libmalloc 只在进程启动时读这个变量：
   `verify.sh` 导出；`q6/__init__.py` 给 spawn 的子进程 setdefault；**直接跑脚本时要自己 `export MallocMediumZone=0`**（见局限 2）。
3. **P2-21 敏感性分析**（Bob，打回 1 次后合并 `b2566df`）：第一版子进程 RSS 479–922MB（Bob 如实写了未达标）。
   原因一是上面的碎片，二是整张网格 24 个 variant 落在**同一个**子进程里 `run_many`，`workers=2` 实际没并行。返工：按 8 个一批分进程、
   默认格按默认值查找（不再硬编码下标）、"默认格排第一"的过拟合标记只用于参数网格——退市回收率 1.0 排第一是因为它是最乐观的假设，改为如实标注。
   合并后我在 Win 上跑出 Windows 专有 bug：`sensitivity.py` 导入了 POSIX 才有的 `resource`，第 4 步收集失败（`aef248d` 上 A/B 都 EXIT=1），
   `f65cfe2` 修复（Win 用 psutil `peak_wset`）。
4. **P2-25 覆盖率**（Bob，`deaad24`）：只加测试不改 src；`feed.py` 25% → 96%、`calendar.py` 68% → 91%。
5. **基准**、**ML walk-forward** 用真快照实跑，结果在第 3 节。

### 2. 证据（真实命令与输出）

**最终验收（Mac，`f65cfe2`）**：`Q6_REQUIRE_SNAPSHOT=1 Q6_SNAPSHOT_ROOT=$PWD/data/snapshots Q6_SNAPSHOT=6252e931a86bda15 bash scripts/verify.sh`

```
== [4/7] uv run pytest tests/unit tests/property
427 passed in 62.76s (0:01:02)
（第 5 步 20 个截断测试文件、第 6 步 3 个一致性文件全部 passed，无 skipped；最高 PEAK_RSS 442.0 MiB = tests/lookahead/test_features_real.py）
== [7/7] snapshot validation
snapshot 6252e931a86bda15 OK
ALL CHECKS PASSED (acceptance mode: real snapshot required)
```

**最终验收（Win，`f65cfe2`）**：`ssh win "... reset --hard origin/rewrite/v6 && powershell -File D:\quant6\run-verify.ps1"`

```
f65cfe2
##### A: acceptance, Q6_SNAPSHOT = absolute dir
EXIT=0
##### B: acceptance, Q6_SNAPSHOT = bare ID
EXIT=0
##### C: acceptance, Q6_SNAPSHOT unset (must fail)
EXIT=1
```
A 的日志：第 4 步 `427 passed in 47.31s`；第 5、6 步全部 passed、无 skipped；最高 PEAK_RSS 436.2 MiB（test_features_real）；
`ALL CHECKS PASSED (acceptance mode: real snapshot required)`。C 在需要快照的测试处 `Failed: Q6_REQUIRE_SNAPSHOT=1 但 Q6_SNAPSHOT 未设置` 并失败（符合预期）。
LightGBM 在 Win 上直接可用（pip 版自带 OpenMP）。

**覆盖率（P2-25 DoD）**：`MallocMediumZone=0 /usr/bin/time -l uv run pytest -q tests/unit tests/property --cov=q6.market --cov=q6.engine --cov=q6.risk --cov=q6.core --cov-report=term-missing`

```
src/q6/core/clock.py             28      0   100%
src/q6/core/pit.py              144      9    94%   45, 66, 82, 148, 180, 184, 190, 200, 235
src/q6/core/types.py            161      6    96%   49, 72, 78, 121, 124, 208
src/q6/engine/broker_sim.py      93      7    92%   34, 43, 49, 62-63, 70, 125
src/q6/engine/event.py          196      9    95%   122, 136-137, 146, 168-169, 233, 252, 266
src/q6/engine/feed.py            80      3    96%   70, 119, 121
src/q6/engine/matching.py       150      5    97%   67, 69, 148, 150, 248
src/q6/engine/vector.py         297     18    94%   86, 111, 135, 144-147, 162, 196, 205, 218, 244, 321, 334, 342, 359, 368, 380
src/q6/market/calendar.py        98      9    91%   61, 64, 69-72, 76-78
src/q6/market/fees_cn.py         86      8    91%   134, 140-142, 166, 172-173, 182
src/q6/market/impact.py          63      3    95%   91-92, 105
src/q6/market/rules_cn.py        98      2    98%   136, 158
src/q6/risk/monitor.py           81      2    98%   47, 49
src/q6/risk/pretrade.py          23      1    96%   16
TOTAL                          1598     82    95%
           327360512  maximum resident set size
```
（`__init__.py` 空文件 100% 略。）逐文件最低 91%。全包 `--cov=q6`：`TOTAL 4510 575 87%`（只报告，不要求）。

**P2-21 敏感性**：`MallocMediumZone=0 /usr/bin/time -l uv run --group report python scripts/run_sensitivity.py`（Bob 实跑，原样）

```
lowvol/cost: CAGR min=0.025081 median=0.070364 max=0.087462; default=0.080848, rank 6/24; max child peak_rss_mb=390.3
lowvol/matching: CAGR min=0.080147 median=0.080746 max=0.081817; default=0.080848, rank 6/12; max child peak_rss_mb=380.8
lowvol/delist: CAGR min=0.072105 median=0.076507 max=0.080848; default=0.080848, rank 1/3 **DEFAULT ASSUMPTION IS THE MOST OPTIMISTIC IN THIS GRID**; recovery=1.0 CAGR over recovery=0.0 by 0.008744 (0.874 pp); max child peak_rss_mb=354.8
lowvol/params: CAGR min=0.049611 median=0.073776 max=0.113808; default=0.080848, rank 4/9; max child peak_rss_mb=413.7
csmf/cost: CAGR min=0.042355 median=0.132593 max=0.168237; default=0.154731, rank 6/24; max child peak_rss_mb=480.0
csmf/matching: CAGR min=0.154631 median=0.154750 max=0.155433; default=0.154731, rank 8/12; max child peak_rss_mb=481.9
csmf/delist: CAGR min=0.148962 median=0.151561 max=0.154731; default=0.154731, rank 1/3 **DEFAULT ASSUMPTION IS THE MOST OPTIMISTIC IN THIS GRID**; recovery=1.0 CAGR over recovery=0.0 by 0.005769 (0.577 pp); max child peak_rss_mb=424.7
All grids peak_rss_mb max=481.9
      446.33 real       687.93 user        52.95 sys
           505344000  maximum resident set size
```
CSV 在 `docs/research/sensitivity/`，PNG 在 `out/sensitivity/`（不入库）。

### 3. 结果（如实，都是**锁箱期前的研究期数据**，不是最终结论）

**ML 排序 walk-forward（P2-18，唯一的样本外结果）**：训练 3 年 / 测试 6 个月 / 步长 6 个月，29 个窗口，purge 21 天，默认参数，
登记簿第 1 条（`registry/runs.jsonl`）。

```
MallocMediumZone=0 /usr/bin/time -l uv run python scripts/rss_guard.py --limit-mb 512 -- uv run python scripts/run_ml_walkforward.py --build-cache
cache=out/ml_cache/6252e931a86bda15_2006-01-01_2024-06-28_e10_h260_b0cfed392375 rows=335260 seconds=1882 finite_share(year file #6)=0.711
PEAK_RSS 408.1 MiB (limit 512)

MallocMediumZone=0 /usr/bin/time -l uv run python scripts/rss_guard.py --limit-mb 512 -- uv run python scripts/run_ml_walkforward.py
 k                   train                    test  ic_mean   ic_t dates
 0 2007-01-04..2009-12-02 2010-01-04..2010-06-30   0.0404   1.21    12
 1 2007-05-28..2010-05-27 2010-07-01..2010-12-31   0.0342   0.86    12
 2 2007-12-03..2010-12-02 2011-01-04..2011-06-30   0.0266   0.70    12
 3 2008-06-02..2011-05-31 2011-07-01..2011-12-30   0.1082   5.77    12
 4 2008-12-01..2011-12-01 2012-01-04..2012-06-29   0.1307   5.14    12
 5 2009-06-01..2012-05-30 2012-07-02..2012-12-31   0.0773   3.10    13
 6 2009-11-30..2012-11-30 2013-01-04..2013-06-28   0.0780   2.75    11
 7 2010-05-27..2013-05-27 2013-07-01..2013-12-31   0.0381   1.57    12
 8 2010-12-02..2013-12-02 2014-01-02..2014-06-30   0.0493   1.62    12
 9 2011-05-30..2014-05-29 2014-07-01..2014-12-31  -0.0041  -0.17    13
10 2011-12-02..2014-12-02 2015-01-05..2015-06-30   0.0208   0.81    12
11 2012-05-29..2015-05-29 2015-07-01..2015-12-31   0.0951   2.68    12
12 2012-12-03..2015-12-02 2016-01-04..2016-06-30   0.0517   2.12    12
13 2013-05-30..2016-05-30 2016-07-01..2016-12-30   0.0915   2.92    13
14 2013-12-02..2016-12-01 2017-01-03..2017-06-30   0.0779   3.43    12
15 2014-06-03..2017-06-01 2017-07-03..2017-12-29   0.0964   2.79    12
16 2014-12-01..2017-11-30 2018-01-02..2018-06-29   0.1198   4.31    12
17 2015-06-01..2018-05-30 2018-07-02..2018-12-28  -0.0371  -0.94    12
18 2015-11-30..2018-11-29 2019-01-02..2019-06-28   0.0988   1.90    12
19 2016-05-30..2019-05-29 2019-07-01..2019-12-31   0.0441   1.57    13
20 2016-12-02..2019-12-02 2020-01-02..2020-06-30   0.1059   3.50    12
21 2017-05-31..2020-05-28 2020-07-01..2020-12-31   0.1006   1.75    12
22 2017-12-04..2020-12-02 2021-01-04..2021-06-30   0.0016   0.04    12
23 2018-05-31..2021-05-31 2021-07-01..2021-12-31  -0.0581  -1.23    12
24 2018-12-03..2021-12-02 2022-01-04..2022-06-30   0.0149   0.37    12
25 2019-05-31..2022-05-31 2022-07-01..2022-12-30  -0.0415  -1.08    13
26 2019-12-02..2022-12-01 2023-01-03..2023-06-30  -0.0455  -1.05    11
27 2020-06-01..2023-05-30 2023-07-03..2023-12-29   0.0530   2.40    13
28 2020-11-30..2023-11-30 2024-01-02..2024-06-28  -0.0137  -0.41     9
pooled OOS rank IC mean=0.0472 std=0.1253 n=349 t=7.04 (样本日间隔 10 天，标签 20 天，相邻样本标签重叠，t 值偏高)

OOS 2010-01-04..2024-06-28 days=3518 windows=29 run_seconds=952
final_equity=3,139,995 CAGR=8.26% vol=22.08% sharpe(rf=0)=0.47 maxDD=-43.27%
fills=14211 fees=416,625 slip+impact=290,002 turnover(annual, one-way)=7.92
reasons: {'LIMIT_QUEUE': 6, 'LOCKED_LIMIT': 5, 'NO_CASH': 51, 'NO_VOLUME': 521, 'PARTIAL': 22, 'SUSPENDED': 61}  delistings=0
PEAK_RSS 462.1 MiB (limit 512)
      968.33 real      1285.60 user       106.43 sys
```
（缓存构建那一行的 `finite_share(year file #6)` 是 `6ccb9bf` 之前的脚本版本打印的——进程启动早于该修复；缓存内容只由特征源码决定，不受影响。）

同期（2010-01-04 至 2024-06-28）对照，基准来自 `MallocMediumZone=0 uv run python scripts/rss_guard.py --limit-mb 512 -- uv run python scripts/run_benchmarks.py`
的逐年收益连乘（PEAK_RSS 309.7 MiB）：

| | 同期累计 | 同期年化 |
|---|---|---|
| ML 排序（样本外，100 万，含费用 + 滑点 + 冲击） | +214.0% | **8.26%** |
| 宇宙等权月度再平衡（含成本，5,000 万） | +36.7% | 2.19% |
| 宇宙买入持有 | +43.1% | 2.52% |
| 沪深 300 价格指数（不含分红） | −3.2% | −0.22% |

逐年（ML / 沪深 300 价格）：2010 +12.95/−12.51，2011 −22.57/−25.01，2012 +21.26/+7.55，2013 +10.32/−7.65，2014 +32.60/+51.66，
**2015 +64.26/+5.58**，2016 −2.05/−11.28，2017 +22.40/+21.78，2018 −25.52/−25.31，2019 +30.05/+36.07，2020 +33.15/+27.21，
2021 +14.83/−5.20，2022 −26.55/−21.63，2023 −6.01/−11.38，2024H1 +0.54/+0.89。

怎么读这组数（不是给结果贴金）：
- 年化 8.26% 但**最大回撤 −43%、波动 22%、夏普 0.47**；14 年半里 5 年亏损，2011 / 2018 / 2022 三年都亏 22–27%。
- 超额**高度集中在 2015 年**（+64% vs 宇宙等权 +39%）和 2012–2013。去掉 2015，ML 同期年化约 4.9%（其余 13.5 年连乘）。
- **信号在衰减**：最近 7 个测试窗口（2021H1–2024H1）里 5 个 rank IC ≤ 0，2021 年以后 IC 均值约 −0.013。按现在的样子拿去实盘，没有证据能赚钱。
- 合并 t=7.04 被高估：样本日每 10 天一个、标签 20 天，相邻样本重叠；窗口级 IC 的 t 只有 29 个点。
- 成本不小：费用 41.7 万 + 滑点冲击 29.0 万（初始 100 万），年化单边换手 7.9 倍。成本敏感性没对 ML 做（P2-21 只做了两个规则策略）。
- 特征集（动量 / 反转 / 低波 / 流动性 / Alpha101 等）是已发表的异象，选择本身带有"事后知道哪些有效"的成分；没有市值数据，无法做规模中性，超额里可能有相当一部分是小盘暴露（宇宙等权 vs 沪深 300 也说明这一点）。
- 基准口径不一致：宇宙等权用 5,000 万资金（冲击更高、偏保守），沪深 300 是价格指数（不含分红，约低估 1–2%/年）。

**规则策略（全区间 2006-01 至 2024-06，样本内、默认参数，P2-21 网格的默认格）**：低波样板 CAGR 8.08%（maxDD −61.6%）；
截面多因子 15.47%（maxDD **−66.9%**，费用 179 万 / 初始 100 万；成本 ×5 时 5.89%）。这两个都**没有**走 walk-forward，因子是我按经验选的，
只能说明引擎能跑、对成本有多敏感，**不能当作策略有效的证据**。低波参数网格里默认格（n=30, lookback=60）排第 4/9，三个邻格更好（最高 11.4%），
按规定没有据此改默认值。

### 4. 对照 `02-任务拆分清单.md` 的 DoD

| ID | DoD | 状态 |
|---|---|---|
| P2-03 | 涨跌停价与真实数据比对全部一致 | 达成（全量比对，比抽 50 个更严） |
| P2-05/06 | 一字板 / 参与率 / 部分成交；hypothesis 不变量、成本不为 0 | 达成 |
| P2-07 | t 收盘信号 t+1 成交有测试；样例策略完整回测 | 达成 |
| P2-08 | 与事件引擎一致（容差定为 1e-12）；测速报告 | 达成（事件引擎样板 32.4s→18.0s；向量引擎样板全区间 16s、多因子 61s，见上表命令） |
| P2-09 | 3 策略 × 3 段，进 CI，逐日偏差表 | 达成（verify 第 6 步） |
| P2-10 | 纯浮亏触发日亏限额；冷却期不开新仓 | 达成 |
| P2-11 | 约束满足；不可行时有降级路径 | 达成 |
| P2-12/13 | 截断测试全过；docstring | 达成 |
| P2-14 | 接口冻结，样板可接入；combo | 达成 |
| P2-15/16/17 | 截断测试；两种引擎；配对形成期不读交易期 | 达成 |
| P2-18 | 截断测试；purge 测试；固定 seed 两次训练逐位一致 | 达成（`test_fit_window_inside_horizon_and_deterministic`） |
| P2-19 | 指标对照已知序列 | 达成 |
| P2-20 | 锁箱期后数据任何路径读不到 | 达成（加载层 + 面板层 + 读数据白名单测试；本轮该测试拦下过我自己的代码） |
| P2-21 | 热力图数据和 PNG；进程峰值 ≤512MB（附记录） | 达成（每格 `peak_rss_mb` 列在 CSV 里） |
| P2-22 | 对照论文数值样例 | 达成 |
| P2-23 | 每次回测追加；git sha / snapshot_id / 配置哈希 | 达成；**但** `run_ml_walkforward.py` 不经 TOML 配置，`config_hash` 为 null（参数全在 params 字段里） |
| P2-24 | 沪深 300、中证 800 等权含成本、买入持有；区间表 | **部分**：沪深 300 / 中证 800 是价格指数；"等权含成本"是对**我们的可投资宇宙**（约 900 只）做的，不是中证 800 成分（没有历史成分表） |
| P2-25 | 核心路径 ≥90% 行覆盖 | 达成（逐文件最低 91%） |

### 5. 没做到 / 已知局限（新增部分；P1 和前文已列的不重复）

1. **RSS 余量**：最紧的几处——敏感性分析 csmf 子进程 481.9MB（余 30MB）、ML walk-forward 462.1MB、`test_features_real` Mac 442–449MB / Win 436–446MB。
   都在 512MB 以内，没有放宽上限；P3 加数据或加特征时会先撞这里。
2. **`MallocMediumZone=0` 要靠入口进程导出**：verify.sh 已导出，`rss_guard` 会在超限时判失败；但直接 `uv run python scripts/xxx.py` 而忘了导出，
   macOS 上 RSS 会翻倍（csmf 808MB）。Windows 不涉及（Arrow 用 system 池）。
3. **ML 结果见第 3 节的全部保留意见**；ML 没做成本敏感性和 Deflated Sharpe（登记簿里 ML 只有 1 次试验，DSR 等 P3 有多次试验后一起算）。
4. **登记簿第一条 `git_dirty=true`**：运行期间我在改 PROGRESS.md（只有文档），代码与 `deaad24` 一致。如实保留，没有删了重跑。
5. `ts_corr` 的修复改变了常数窗口上的输出（±inf → NaN）。截面多因子的默认 6 个因子不含 `ts_corr`（一致性套件和截断测试照常通过）；Alpha101 子集里用到它的 10 个因子数值会变，ML 的 75 个特征里含这些因子——本次 walk-forward 用的是修复后的版本（修复前合成数据上实测会出 inf，`feature_matrix` 遇 inf 直接报错，不会静默）。
6. P2-03 规则表、退市回收率默认 1.0（实测乐观 0.58–0.87pp/年）、没有行业 / 市值 / 财务数据等，见前文。

### 6. 状态

- 分支 `rewrite/v6` 已 push（`f65cfe2` + 本文档提交）；`main` 仍是 `ad76ed1`，未动。
- Bob 的 worktree 全部已合并；没有在途任务。
- **停在这里等 Adam 验收 P2，未开工 P3。**
