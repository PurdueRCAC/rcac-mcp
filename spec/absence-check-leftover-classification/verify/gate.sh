#!/bin/sh
# Phase gate for absence-check-leftover-classification. From the worktree root:
#
#   sh spec/absence-check-leftover-classification/verify/gate.sh P1       every part below, in order
#   sh spec/absence-check-leftover-classification/verify/gate.sh <part>   one part: static, tests,
#                                                                         foreign, nogit, mutants
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
BASE=5cf6e40  # moved base: develop 5cf6e40 (safe-execution) merged in, 2026-10-23; was 90b0938 (2026-10-22), bfa0595 before
phase=${1:?usage: gate.sh P1 or static|tests|foreign|nogit|mutants}

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

# Inside a git hook's environment (its GIT_INDEX_FILE) and with a HOME whose git configuration is
# malformed, every listed id still passes with git and CI=true: no control reads the hook's index
# or the developer's own configuration. The interpreter is found first, so uv never sees that HOME.
foreign() {
    python=$(uv run -q python -c 'import sys; print(sys.executable)') || fail 'no interpreter'
    tmp=$(mktemp -d)
    mkdir -p "$tmp/home/.config/git"
    printf '[core\n' >"$tmp/home/.gitconfig"
    printf '[core\n' >"$tmp/home/.config/git/config"
    GIT_INDEX_FILE="$tmp/hook-index" HOME="$tmp/home" XDG_CONFIG_HOME="$tmp/home/.config" CI=true \
        "$python" -m pytest -q -p no:cacheprovider --junitxml="$tmp/foreign.xml" tests/test_registry.py ||
        { rm -rf "$tmp"; fail 'the slice is red under a hook environment and a malformed HOME'; }
    py outcomes "$tmp/foreign.xml" all-pass || { rm -rf "$tmp"; fail 'an id is missing or not passed (foreign)'; }
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
    P1) static; tests; foreign; nogit; mutants ;;
    static | tests | foreign | nogit | mutants) "$phase" ;;
    *) fail "unknown phase or part $phase" ;;
esac
echo "gate[$phase]: ok"
