#!/bin/sh
# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT
#
# Run a command against a throwaway cluster-mcp sandbox so factory verify
# commands and review CLI drives never touch the developer's real home
# directory, config, credentials — or a real HPC cluster.
#
# This is the analogue of a pytest tmp_path fixture for CLI drives. It is the
# only safe way to exercise tools that WRITE (write_file, upload_file,
# run_command), because in `local` execution mode those act on the machine you
# are sitting at, as you.
#
# Three protections, in order of importance:
#
#   1. FAIL CLOSED ON SSH. RCAC_SSH_HOST is unset, so a bare `rcac-mcp` (whose
#      stdio transport defaults to `ssh` execution) raises
#      "SSH host required: use --ssh-host or set RCAC_SSH_HOST" instead of
#      opening a session on a production cluster. Drive `-e local` explicitly.
#      This is a guard against accidents, not against an explicit --ssh-host.
#   2. HOME IS THE SANDBOX. `~` expansion inside a tool call (`write_file`,
#      `read_file`, `cwd="~/jobs"`) lands in the throwaway dir. XDG config and
#      cache follow it, so per-user state never lands in the real ~/.config.
#   3. AUTH ENV IS BLANK. JWT_SECRET and the OIDC_* / RCAC_USER_MAP variables are
#      unset so a drive cannot accidentally mint or accept a token signed with
#      the developer's real secret.
#
# Usage:
#   .agents/factory/bin/sandbox.sh uv run rcac-mcp --help
#   .agents/factory/bin/sandbox.sh sh -c "uv run rcac-mcp -e local --generate-token"
#
# CWD IS THE SANDBOX, NOT THE REPO. That is protection 2 working as intended, but it breaks any
# tool that discovers the project by walking up from the working directory. `uv run` is handled
# (UV_PROJECT below), but pytest is not: run from here it sets rootdir to the throwaway dir, never
# loads pyproject.toml, and so silently loses --strict-markers and every registered marker. For a
# test drive, step back into the repo explicitly — you keep the HOME, auth, and fail-closed-SSH
# guarantees and give up only cwd containment, which pytest's own tmp_path fixture replaces:
#
#   .agents/factory/bin/sandbox.sh sh -c 'cd "$CLUSTER_MCP_REPO" && uv run pytest -v -m integration'
#
# The sandbox directory is created under $TMPDIR and removed on exit (any path).
set -eu

# Capture the repo root before we cd into the sandbox below. `uv run` discovers
# the project by walking up from the working directory, so once cwd is the /tmp
# sandbox it can no longer find pyproject.toml; UV_PROJECT pins discovery back
# to the repo. (Callers invoke this from the repo root, per the usage examples.)
root="$(pwd)"

# Resolve uv's cache from the REAL home before HOME is replaced, or every drive
# re-downloads the whole dependency tree into a directory we then delete.
UV_CACHE_DIR="${UV_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/uv}"

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/cluster-mcp-sandbox.XXXXXX")"
trap 'rm -rf "$sandbox"' EXIT INT TERM

HOME="$sandbox"
XDG_CONFIG_HOME="$sandbox/.config"
XDG_CACHE_HOME="$sandbox/.cache"
XDG_DATA_HOME="$sandbox/.local/share"
UV_PROJECT="$root"
mkdir -p "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_DATA_HOME"
export HOME XDG_CONFIG_HOME XDG_CACHE_HOME XDG_DATA_HOME UV_PROJECT UV_CACHE_DIR

# Fail closed rather than reaching a real cluster or a real identity provider.
unset RCAC_SSH_HOST RCAC_USER_MAP JWT_SECRET \
      OIDC_CONFIG_URL OIDC_CLIENT_ID OIDC_CLIENT_SECRET MCP_BASE_URL

# A marker a test or tool can assert on to prove it is running contained, and the way back to the
# repo for a command that must be run from it (see the pytest note in the usage header).
CLUSTER_MCP_SANDBOX="$sandbox"
CLUSTER_MCP_REPO="$root"
export CLUSTER_MCP_SANDBOX CLUSTER_MCP_REPO

# Run inside the sandbox so relative writes (e.g. `sh -c "… > out.txt"`) stay
# contained instead of escaping into the working tree, where cm-build's
# `git add -A` would sweep them into the atomic commit.
cd "$sandbox"
"$@"
