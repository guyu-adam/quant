#!/usr/bin/env bash
set -euo pipefail

step=0

run_step() {
  local number="$1"
  local name="$2"
  shift 2
  step="$number"
  printf '== [%s/6] %s\n' "$number" "$name"
  local started finished elapsed
  started=$(date +%s)
  if "$@"; then
    finished=$(date +%s)
    elapsed=$((finished - started))
    printf 'TIME [%s/6] %s: %ss\n' "$number" "$name" "$elapsed"
  else
    local status=$?
    finished=$(date +%s)
    elapsed=$((finished - started))
    printf 'TIME [%s/6] %s: %ss\n' "$number" "$name" "$elapsed"
    printf 'FAILED at step %s\n' "$number" >&2
    exit "$status"
  fi
}

rss_limit="${Q6_RSS_LIMIT_MB:-512}"
rss_guard() {
  uv run python scripts/rss_guard.py --limit-mb "$rss_limit" -- "$@"
}

# 第 5 步每个测试文件单独一个进程、单独受 RSS 上限约束：几个真实快照测试各自 <512MB，
# 但放在同一个 pytest 进程里，macOS malloc 不归还页面，峰值会累加到 584MB（P2-13 合并时实测）。
lookahead_per_file() {
  local f
  for f in tests/lookahead/test_*.py; do
    printf -- '-- %s\n' "$f"
    rss_guard uv run pytest "$f" || return 1
  done
}

run_step 1 'uv sync --frozen' uv sync --frozen
run_step 2 'uv run ruff check src tests scripts' uv run ruff check src tests scripts
run_step 3 'uv run python -m q6.lint.lookahead_ast src' uv run python -m q6.lint.lookahead_ast src
run_step 4 'uv run pytest tests/unit tests/property' rss_guard uv run pytest tests/unit tests/property
run_step 5 'uv run pytest tests/lookahead (one process per file)' lookahead_per_file
run_step 6 'snapshot validation' rss_guard uv run python scripts/check_snapshot.py

if [[ "${Q6_REQUIRE_SNAPSHOT:-}" == 1 ]]; then
  printf 'ALL CHECKS PASSED (acceptance mode: real snapshot required)\n'
else
  printf 'ALL CHECKS PASSED (dev mode: missing snapshot is skipped, NOT valid for acceptance; set Q6_REQUIRE_SNAPSHOT=1)\n'
fi
