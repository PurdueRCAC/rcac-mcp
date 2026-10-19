#!/bin/sh
# P1's gate: the site reader and composer. Run from the root of the tree being built.
# Footprint: one pytest at a time (one process, a few short bin/sandbox.sh bash children, each
# awaited), then site_gate.py (the same shape); under 300 MB, about 10 s, no network.
set -eu
V=spec/agents-d-instructions/verify
T=tests/test_site.py
uv run pytest -q $T::test_local_read_takes_the_eligible_files_in_byte_order $T::test_local_read_states \
  $T::test_remote_answers $T::test_remote_script_matches_local_read \
  $T::test_bounded_read_times_out_and_contains_errors $T::test_compose_keeps_its_contract_at_every_budget \
  $T::test_compose_fits_a_later_file_after_one_left_out $T::test_compose_without_files_serves_the_reason \
  $T::test_units_count_utf16_code_units $T::test_names_and_text_keep_their_shape \
  $T::test_a_name_that_is_not_utf8_reads_as_replacement_text \
  $T::test_a_budget_that_serves_a_good_read_serves_a_failed_one tests/test_hygiene.py
uv run python $V/site_gate.py
