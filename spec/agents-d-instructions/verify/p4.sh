#!/bin/sh
# P4's gate: documentation, the ledger and the mutants, then the profile's full verify.
# Usage: p4.sh [docs|mutants|full|all] [BASE_REF]. `docs` is the pre-push and phase-only part
# (seconds); `mutants` runs only this cycle's mutation cases; `full` only the profile's verify;
# `all` (the default) runs the three in that order. Footprint: one process tree at a time; the
# mutation children are sequential; the full verify is about 7 min on mac-m2, under 1 GB.
set -eu
V=spec/agents-d-instructions/verify
MODE=${1:-all}
[ $# -gt 0 ] && shift
case $MODE in
  docs|mutants) uv run python $V/docs_gate.py "$MODE" "$@" ;;
  full) ;;
  all) uv run python $V/docs_gate.py all "$@" ;;
  *) echo "p4.sh: unknown mode $MODE" >&2; exit 2 ;;
esac
if [ "$MODE" = full ] || [ "$MODE" = all ]; then
  uv lock --check
  uv run pytest -q
  CLUSTER_MCP_TEST_ERA=modern uv run pytest -q
  bin/sandbox.sh uv run rcac-mcp --help >/dev/null
fi
