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
