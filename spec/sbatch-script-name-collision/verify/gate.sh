#!/bin/sh
# Phase gate for sbatch-script-name-collision. From the worktree root:
#
#   sh spec/sbatch-script-name-collision/verify/gate.sh P1|P2     every part of that phase, in order
#   sh spec/sbatch-script-name-collision/verify/gate.sh <part>    one part: static, tests1, mutants1,
#                                                                 surface, tests2, mutants2, full
#
# The phase-only parts (tests1, mutants1, tests2, mutants2) are what code-build's fail-once mutation
# targets; `full` is the profile's whole verify and runs only in P2. No phase has an outward step,
# so there is no pre-push mode.
#
# Offline and deterministic: UV_OFFLINE is set, the base revision is read with git, every test
# drive and every mutant child runs inside bin/sandbox.sh (a fresh HOME, SSH failing closed, auth
# blank), the ssh rows talk to the suite's in-process loopback server, and nothing reaches a
# network or a cluster.
#
# Footprint: one child at a time (uv, then one pytest or one mutant child pytest, each launching
# bash or a child interpreter one call at a time, two at once only inside the overlap rows); peak
# under 1 GB. No background work, so nothing outlives the gate. Each part removes its own
# temporary directory.
set -eu
# Offline is enforced, not assumed: a step that would reach the network fails instead.
export UV_OFFLINE=1
# Test ids carry brackets; nothing here should glob them.
set -f
cd "$(git rev-parse --show-toplevel)"
V=spec/sbatch-script-name-collision/verify
BASE=d34de62  # origin/develop when the cycle was shaped (2026-10-23)
phase=${1:?usage: gate.sh P1|P2 or static|tests1|mutants1|surface|tests2|mutants2|full}

fail() { echo "gate[$phase]: $*" >&2; exit 1; }
check() { uv run -q python "$V/check.py" "$@"; }

# Every id in a list passes in both protocol eras, run inside the sandbox from the repo root.
ids() {
    list=$1
    targets=$(check targets "$list") || fail "cannot read $list"
    tmp=$(mktemp -d)
    for era in legacy modern; do
        # shellcheck disable=SC2086
        bin/sandbox.sh sh -c 'cd "$CLUSTER_MCP_REPO" && era=$1 junit=$2 && shift 2 &&
            CLUSTER_MCP_TEST_ERA=$era uv run pytest -q -p no:cacheprovider --junitxml="$junit" "$@"' \
            sh "$era" "$tmp/$era.xml" $targets ||
            { rm -rf "$tmp"; fail "a test in $list is red in the $era era"; }
    done
    check outcomes "$list" "$tmp/legacy.xml" "$tmp/modern.xml" ||
        { rm -rf "$tmp"; fail "an id in $list is missing, repeated, skipped or not passed"; }
    rm -rf "$tmp"
}

# What the fix may touch, and how the distinguishing part is drawn.
static() { check static --base "$BASE" || fail 'static checks are red'; }

tests1() { uv lock --check || fail 'uv.lock is stale'; ids "$V/ids-P1.txt"; }

# The new tests are not hollow: a per-second name and a per-process counter each turn them red.
mutants1() {
    uv run -q python tests/mutants.py script-name-per-second script-name-process-counter ||
        fail 'a planted naming defect survived'
}

# The one named served-surface delta, at most 2,000 characters (this cycle's cap, not a suite-wide
# rule), recorded in AGENTS.md.
surface() { check surface --base "$BASE" || fail 'surface checks are red'; }

tests2() { ids "$V/ids-P2.txt"; }

# The docstring test is not hollow: a shorter token than the docstring states turns it red.
mutants2() { uv run -q python tests/mutants.py script-token-short || fail 'a planted docstring mismatch survived'; }

# The profile's verify, whole, with each pytest run inside the sandbox.
full() {
    uv lock --check || fail 'uv.lock is stale'
    bin/sandbox.sh sh -c 'cd "$CLUSTER_MCP_REPO" && uv run pytest -q -p no:cacheprovider' ||
        fail 'the suite is red in the handshake era'
    bin/sandbox.sh sh -c 'cd "$CLUSTER_MCP_REPO" && CLUSTER_MCP_TEST_ERA=modern uv run pytest -q -p no:cacheprovider' ||
        fail 'the suite is red in the 2026-07-28 era'
    bin/sandbox.sh uv run rcac-mcp --help >/dev/null || fail 'rcac-mcp --help failed in the sandbox'
}

case $phase in
    P1) static; tests1; mutants1 ;;
    P2) static; surface; tests1; tests2; mutants2; full ;;
    static | tests1 | mutants1 | surface | tests2 | mutants2 | full) "$phase" ;;
    *) fail "unknown phase or part $phase" ;;
esac
echo "gate[$phase]: ok"
