#!/bin/sh
# P2's gate: startup reads the site once and serves the composed instructions. Run from the root
# of the tree being built. Footprint: the named tests in each era in turn (test_transports starts
# one sandboxed server at a time), then serve_gate.py instructions (one server at a time, its
# process group killed before the next) and fixture_deltas.py; under 500 MB, about 2.5 min.
set -eu
V=spec/agents-d-instructions/verify
S=tests/test_site_startup.py
TESTS="$S::test_startup_reads_once_where_the_mode_says $S::test_a_hung_read_does_not_hold_startup
  $S::test_budget_resolution $S::test_cli_refuses_a_budget_too_small tests/test_instructions.py
  tests/test_protocol.py tests/test_surface.py tests/test_transports.py::test_transport_lists_expected_tools
  tests/test_exec_mode.py tests/test_registry.py tests/test_site.py tests/test_hygiene.py"
# Word splitting of $TESTS is intended: one argument per test id.
uv run pytest -q $TESTS
CLUSTER_MCP_TEST_ERA=modern uv run pytest -q $TESTS
uv run python $V/serve_gate.py instructions
uv run python $V/fixture_deltas.py check "$@"
