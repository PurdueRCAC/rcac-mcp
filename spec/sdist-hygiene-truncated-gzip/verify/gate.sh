#!/bin/sh
# Phase gate for sdist-hygiene-truncated-gzip. From the worktree root:
#
#   sh spec/sdist-hygiene-truncated-gzip/verify/gate.sh P1        every part below, in order
#   sh spec/sdist-hygiene-truncated-gzip/verify/gate.sh <part>    one part: static, tests,
#                                                                 drive, control, mutants
#
# Offline and deterministic: every planted sdist is built from the standard library with a fixed
# seed, and the base revision is read with `git show`. Nothing here builds a real sdist or reaches
# the network. Each part cleans up its own temporary directory.
#
# Footprint: one child process at a time (uv, then one python or pytest); peak under 300 MB.
# No background work, so nothing outlives the gate. P1 takes about 30 s on mac-m2.
set -eu
# Offline is enforced, not assumed: a step that would reach the network fails instead.
export UV_OFFLINE=1
cd "$(git rev-parse --show-toplevel)"
V=spec/sdist-hygiene-truncated-gzip/verify
BASE=5153e47
phase=${1:?usage: gate.sh P1 or static|tests|drive|control|mutants}

fail() { echo "gate[$phase]: $*" >&2; exit 1; }
py() { uv run -q python "$@"; }

# What the fix must leave alone: ci.yml, the recipe step, the furniture set, the root rule, main.
static() { py "$V/static.py" --base "$BASE" || fail 'static checks are red'; }

# Every named test id passes. Naming them makes a renamed or dropped test an error, not a skip.
tests() {
    uv lock --check || fail 'uv.lock is stale'
    # shellcheck disable=SC2046
    uv run pytest -q -p no:cacheprovider $(sed -e '/^#/d' -e '/^$/d' -e 's|^|tests/test_sdist_hygiene.py::|' "$V/ids.txt") ||
        fail 'a named hygiene test id is red or missing'
}

# The real command over every plant: exact lines, exit status, no traceback, base parity.
drive() { py "$V/drive.py" --base "$BASE" || fail 'the drive is red'; }

# The drive is not blind: against the base script it sees each known defect.
control() {
    red=truncated-half,not-a-tarball,crc-flipped,header-corrupt-mid,last-header-damaged,member-after-end
    red=$red,gzip-trailing-garbage,xz-truncated,mixed,no-end-marker,member-after-long-zero-tail
    red=$red,bz2-second-stream-damaged,xz-second-stream-damaged
    py "$V/drive.py" --base "$BASE" --script-rev "$BASE" --expect-red "$red" ||
        fail 'the drive misses a defect the base script has'
}

# The tests are not hollow: each way of breaking the fix turns named tests red.
mutants() { py "$V/mutants.py" || fail 'a mutant survived'; }

case $phase in
    P1) static; tests; drive; control; mutants ;;
    static | tests | drive | control | mutants) "$phase" ;;
    *) fail "unknown phase or part $phase" ;;
esac
echo "gate[$phase]: ok"
