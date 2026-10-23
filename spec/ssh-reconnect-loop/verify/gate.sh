#!/bin/sh
# Phase gate for ssh-reconnect-loop, run from the worktree root:
#   sh spec/ssh-reconnect-loop/verify/gate.sh P1|P2|tests|mutants
#
# Modes:
#   P1, P2 — the phase's named test ids in both protocol eras, then its mutation cases, then the
#            sandboxed CLI drive; P2 re-runs P1's ids, since it builds on them.
#   tests  — every id this cycle names, in both eras, and nothing else (no mutants).
#   mutants — only the cycle's mutation cases (for a fail-once mutation, or a last-phase rerun).
#
# Footprint: one process at a time. pytest takes about 300 MB. tests/mutants.py runs one child copy
# of the package at a time, about 2 s per case (13 cases, under a minute). Nothing runs in the background, so there is no
# process tree to clean up. Offline: no real ssh and no host. The in-process SSH server binds
# 127.0.0.1 only, and the CLI drive goes through bin/sandbox.sh, which fails closed on SSH.
#
# Named node ids, never a marker or -k selection: pytest exits 4 on an id that does not exist, so
# a renamed or missing test turns the gate red instead of selecting nothing.
set -eu

T=tests/test_ssh_exec.py
P1_IDS="tests/test_startup.py tests/test_mutations.py
$T::test_a_reconnect_that_cannot_succeed_fails_within_the_bound
$T::test_a_reconnect_refused_authentication_fails_at_once
$T::test_a_reconnect_bound_grows_with_a_longer_connect_timeout
$T::test_a_reconnect_bound_reads_the_connect_timeout
$T::test_a_reconnect_attempt_that_hangs_is_abandoned_at_the_bound
$T::test_a_reconnect_that_blocks_building_the_connection_is_abandoned
$T::test_concurrent_reconnects_publish_one_connection
$T::test_a_reconnect_that_succeeds_at_the_deadline_is_owned_once
$T::test_a_failed_reconnect_of_a_transfer_raises_naming_the_host
$T::test_reconnect_texts_state_the_bound
$T::test_a_lost_connection_is_never_did_not_start"
P2_IDS="$T::test_a_reconnect_after_the_host_returns_gives_the_same_result
$T::test_a_failed_reconnect_leaves_the_server_serving
$T::test_a_stalled_reconnect_is_abandoned_against_a_real_handshake
$T::test_a_reconnect_failure_reaches_each_tool_as_not_run
$T::test_sacct_spends_one_reconnect_window
$T::test_a_reconnect_failure_is_never_cached_as_cluster_context"
P1_MUTANTS="ssh-reconnect-unbounded-poll ssh-reconnect-unbounded-retry ssh-reconnect-reads-as-lost
ssh-reconnect-retries-refused-auth ssh-reconnect-late-success-leaks
startup-host-unnamed startup-no-connect-timeout staged-checksum-unchecked transfer-cause-unnamed"
P2_MUTANTS="ssh-reconnect-gives-up-for-good sbatch-not-started-reads-as-failed sacct-whoami-unchecked
context-caches-not-started"

both_eras() {
    # Word splitting of the id list is intended: one id per line, no spaces in any id.
    uv run pytest -q $1
    CLUSTER_MCP_TEST_ERA=modern uv run pytest -q $1
}

mode=${1:?usage: gate.sh P1|P2|tests|mutants}
uv lock --check
case $mode in
    P1)
        both_eras "$P1_IDS"
        uv run python tests/mutants.py $P1_MUTANTS
        bin/sandbox.sh uv run rcac-mcp --help >/dev/null
        ;;
    P2)
        both_eras "$P1_IDS
$P2_IDS"
        uv run python tests/mutants.py $P1_MUTANTS $P2_MUTANTS
        bin/sandbox.sh uv run rcac-mcp --help >/dev/null
        ;;
    tests)
        both_eras "$P1_IDS
$P2_IDS"
        ;;
    mutants)
        uv run python tests/mutants.py --self-check $P1_MUTANTS $P2_MUTANTS
        ;;
    *)
        echo "gate.sh: unknown mode $mode (P1, P2, tests or mutants)" >&2
        exit 2
        ;;
esac
echo "gate.sh $mode: green" >&2
