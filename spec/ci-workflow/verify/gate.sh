#!/bin/sh
# Phase gates for ci-workflow. From the worktree root:
#
#   sh spec/ci-workflow/verify/gate.sh P<n>       the cumulative gate for phase n (P1..P5)
#   sh spec/ci-workflow/verify/gate.sh prepush    the check that must pass before any push:
#                                                 clean tree, then everything local on a fresh
#                                                 clone of HEAD (committed state, not the tree)
#   sh spec/ci-workflow/verify/gate.sh landing    the profile ci row's read for HEAD: at least one
#                                                 push run of ci.yml, every one completed/success
#
# Local phases (P1-P3) read a snapshot of what `git add -A` would commit; prepush, P4 and P5
# read HEAD. The GitHub phases (P4, P5) only read: every push they depend on is a separate,
# approved act in the phase checklist, never something this gate does.
set -eu
root=$(git rev-parse --show-toplevel)
cd "$root"
V=spec/ci-workflow/verify
phase=${1:?usage: gate.sh P1..P5 or prepush}
scratch=$(mktemp -d "${TMPDIR:-/tmp}/ci-gate.XXXXXX")
trap 'rm -rf "$scratch"' EXIT HUP INT TERM

fail() { echo "gate[$phase]: $*" >&2; exit 1; }
py() { uv run -q --with pyyaml==6.0.3 python "$@"; }
lint() {
    uvx -q --with shellcheck-py==0.11.0.1 --from actionlint-py==1.7.12.25 actionlint "$@" || fail 'actionlint reported findings'
    # Online, so zizmor also checks each pin against GitHub (ref-version-mismatch, impostor-commit).
    # The token only reads the public API.
    token=$(gh auth token) || fail 'zizmor needs a GitHub token to check pins: run gh auth login'
    GH_TOKEN=$token uvx -q zizmor@1.31.1 --no-progress "$@" || fail 'zizmor reported findings'
}

# Every named test id passes, read from the JUnit report (never the terminal).
named_tests() {
    report="$scratch/junit.xml"
    uv run pytest -q -p no:cacheprovider tests/test_sdist_hygiene.py "--junitxml=$report" > /dev/null || fail 'tests/test_sdist_hygiene.py is red'
    py "$V/junit_ids.py" "$report" "$V/ids.txt" || fail 'a named hygiene test id did not pass'
}

p1() {
    py "$V/static.py" hygiene
    named_tests
    py "$V/hygiene_mutants.py" --from "$1"
}

p2() {
    p1 "$1"
    py "$V/static.py" hygiene workflow agents controls
    lint .github/workflows/ci.yml .github/workflows/mutants.yml
    py "$V/replay.py" --from "$1"
    py "$V/replay.py" --from "$1" --job mutants
    py "$V/replay.py" --from "$1" --job mutants --plant empty-cases --expect-red 'test -n' --expect-log 'no mutation cases found'
}

p3() {
    p2 "$1"
    py "$V/replay.py" --from "$1" --plant test-red --expect-red 'uv run pytest -q' --expect-log 'planted red'
    py "$V/replay.py" --from "$1" --plant agents-furniture --expect-red sdist_hygiene --expect-log '.agents/planted.md'
    py "$V/replay.py" --from "$1" --plant security-furniture --expect-red sdist_hygiene --expect-log '.security/planted.md'
    py "$V/evidence.py" local
}

prepush() {
    test -z "$(git status --porcelain)" || fail 'the tree is dirty; commit or stash first'
    test "$(git branch --show-current)" = ci/ci-workflow || fail 'not on ci/ci-workflow'
    git clone -q --shared "$root" "$scratch/repo"
    git -C "$scratch/repo" checkout -q --detach "$(git rev-parse HEAD)"
    py "$V/static.py" --root "$scratch/repo" hygiene workflow agents controls
    (cd "$scratch/repo" && lint .github/workflows/ci.yml .github/workflows/mutants.yml)
    p3 HEAD
}

case $phase in
    P1) p1 worktree ;;
    P2) p2 worktree ;;
    P3) p3 worktree ;;
    prepush) prepush ;;
    P4) py "$V/evidence.py" local green ;;
    P5) py "$V/evidence.py" local green red ledger ;;
    landing) py "$V/evidence.py" landing ;;
    *) fail "unknown phase $phase" ;;
esac
echo "gate[$phase]: ok"
