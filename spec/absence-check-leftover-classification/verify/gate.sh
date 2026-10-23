#!/bin/sh
# Phase gate for absence-check-leftover-classification. From the worktree root:
#
#   sh spec/absence-check-leftover-classification/verify/gate.sh P1       every part below, in order
#   sh spec/absence-check-leftover-classification/verify/gate.sh <part>   one part: static, tests,
#                                                                         nogit, mutants
#
# Offline and deterministic: every git-backed test builds its own repository under tmp_path, and the
# base revision is read with `git show`. Nothing here reaches the network. Each part cleans up its
# own temporary directory.
#
# Footprint: one child process at a time (uv, then one python or pytest, which runs git through
# bin/sandbox.sh one call at a time); peak under 300 MB. No background work, so nothing outlives the
# gate. P1 takes about a minute on mac-m2.
set -eu
# Offline is enforced, not assumed: a step that would reach the network fails instead.
export UV_OFFLINE=1
cd "$(git rev-parse --show-toplevel)"
V=spec/absence-check-leftover-classification/verify
BASE=90b0938  # moved base: develop 90b0938 (release 0.1.2) merged in, 2026-10-22; was bfa0595, rebased onto 2026-10-22
phase=${1:?usage: gate.sh P1 or static|tests|nogit|mutants}

fail() { echo "gate[$phase]: $*" >&2; exit 1; }
py() { uv run -q python "$V/check.py" "$@"; }

# What the change must leave alone: the verdict, the names, the base ids, other paths, AGENTS.md.
static() { py static --base "$BASE" || fail 'static checks are red'; }

# With git and CI=true, every listed id passes in both protocol eras, and the sandbox rule holds.
tests() {
    uv lock --check || fail 'uv.lock is stale'
    tmp=$(mktemp -d)
    for era in legacy modern; do
        CI=true CLUSTER_MCP_TEST_ERA=$era uv run pytest -q -p no:cacheprovider --junitxml="$tmp/$era.xml" \
            tests/test_registry.py tests/test_hygiene.py::test_cli_drives_are_sandboxed ||
            { rm -rf "$tmp"; fail "the suite slice is red in the $era era"; }
        py outcomes "$tmp/$era.xml" all-pass || { rm -rf "$tmp"; fail "an id is missing or not passed ($era)"; }
    done
    rm -rf "$tmp"
}

# Without git on PATH: off CI the git-backed ids skip, naming git, and the rest pass; on CI the
# git-backed ids fail, naming git, and the rest still pass. PATH is a farm of the few programs the
# slice runs, with no git among them.
nogit() {
    tmp=$(mktemp -d)
    mkdir "$tmp/bin"
    for program in uv sh env dirname mktemp mkdir rm; do
        found=$(command -v "$program") || { rm -rf "$tmp"; fail "$program is not on PATH"; }
        ln -s "$found" "$tmp/bin/$program"
    done
    if PATH="$tmp/bin" command -v git >/dev/null 2>&1; then rm -rf "$tmp"; fail 'the farm has git'; fi
    PATH="$tmp/bin" CI= uv run pytest -q -p no:cacheprovider --junitxml="$tmp/local.xml" tests/test_registry.py ||
        { rm -rf "$tmp"; fail 'the slice is red without git off CI'; }
    py outcomes "$tmp/local.xml" no-git-local || { rm -rf "$tmp"; fail 'without git off CI'; }
    if PATH="$tmp/bin" CI=true uv run pytest -q -p no:cacheprovider --junitxml="$tmp/ci.xml" tests/test_registry.py; then
        rm -rf "$tmp"; fail 'the slice is green without git on CI'
    fi
    py outcomes "$tmp/ci.xml" no-git-ci || { rm -rf "$tmp"; fail 'without git on CI'; }
    rm -rf "$tmp"
}

# The tests are not hollow: each way of breaking the explanation turns named tests red.
mutants() { py mutants || fail 'a mutant survived'; }

case $phase in
    P1) static; tests; nogit; mutants ;;
    static | tests | nogit | mutants) "$phase" ;;
    *) fail "unknown phase or part $phase" ;;
esac
echo "gate[$phase]: ok"
