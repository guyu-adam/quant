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

run_snapshot() {
  if [[ -z "${Q6_SNAPSHOT:-}" ]]; then
    printf 'SKIP snapshot (Q6_SNAPSHOT unset)\n'
    return 0
  fi
  uv run python -c 'import os; from pathlib import Path; from q6.data.snapshot import verify_snapshot; verify_snapshot(Path(os.environ.get("Q6_SNAPSHOT_ROOT", "data/snapshots")), os.environ["Q6_SNAPSHOT"])'
}

run_step 1 'uv sync --frozen' uv sync --frozen
run_step 2 'uv run ruff check src tests scripts' uv run ruff check src tests scripts
run_step 3 'uv run python -m q6.lint.lookahead_ast src' uv run python -m q6.lint.lookahead_ast src
run_step 4 'uv run pytest tests/unit tests/property' uv run pytest tests/unit tests/property
run_step 5 'uv run pytest tests/lookahead' uv run pytest tests/lookahead
run_step 6 'snapshot validation' run_snapshot

printf 'ALL CHECKS PASSED\n'
