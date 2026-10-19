#!/bin/sh
# P3's gate: cluster_context and rcac://context serve the startup text. Run from the root of the
# tree being built. Footprint: the named tests in each era in turn, then serve_gate.py context
# (one server at a time) and fixture_deltas.py; under 500 MB, about 1.5 min.
set -eu
V=spec/agents-d-instructions/verify
C=tests/test_context.py
TESTS="$C::test_tool_and_resource_serve_the_startup_text $C::test_delegate_users_get_identical_text
  $C::test_served_instructions_are_composed_from_the_site $C::test_nothing_reads_the_site_after_construction
  $C::test_a_changed_directory_changes_nothing_served $C::test_an_unmapped_user_reads_the_resource_and_is_refused_the_tool
  tests/test_instructions.py tests/test_tools.py tests/test_no_executor.py tests/test_registry.py
  tests/test_surface.py tests/test_protocol.py tests/test_hygiene.py"
# Word splitting of $TESTS is intended: one argument per test id.
uv run pytest -q $TESTS
CLUSTER_MCP_TEST_ERA=modern uv run pytest -q $TESTS
uv run python $V/serve_gate.py context
uv run python $V/fixture_deltas.py check "$@"
