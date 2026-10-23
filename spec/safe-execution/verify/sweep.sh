#!/bin/sh
# Final gate for safe-execution (TECH.md P7). Run from the worktree root.
# 1. No executor caller but run_command hands the executor a command string.
# 2. No text outside the retained records still describes the old interpreter, the old timeout
#    contract, or the heredoc writes as the state of the world.
# 3. Every planted violation in tests/mutants.py is killed for its stated reason.
set -u
status=0

# Every call `<x>.run(...)` in the package whose command argument is a string literal, an f-string
# or a string concatenation (an AST scan, so a call split across lines is seen too).
uv run python - <<'PY' || status=1
import ast, pathlib, sys
bad = []
for f in sorted(pathlib.Path('src/rcac_mcp').rglob('*.py')):
    for node in ast.walk(ast.parse(f.read_text(), str(f))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'run':
            arg = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == 'command'), None)
            if isinstance(arg, (ast.JoinedStr, ast.BinOp)) or (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                bad.append(f'{f}:{node.lineno}')
if bad:
    print('FAILED  a caller passes a command string:', *bad, sep='\n  ')
    sys.exit(1)
PY
# run_command is the only tool that passes a str through; it passes its own parameter.
grep -nE 'executor\.run\(command' src/rcac_mcp/tools/shell.py >/dev/null || {
  echo "FAILED  run_command no longer passes its command through"; status=1; }

# Old phrasings: each fixed string is one claim this cycle retires (found at 04e04c3).
while IFS= read -r pat; do
  [ -z "$pat" ] && continue
  found=$(grep -rnF -- "$pat" src AGENTS.md README.md SECURITY.md 2>/dev/null || true)
  if [ -n "$found" ]; then
    echo "FAILED  retired text still present: $pat"; echo "$found"; status=1
  fi
done <<'PATTERNS'
locally via $SHELL
locally via `$SHELL`
Uses $SHELL for command execution
local $SHELL
`subprocess` via `$SHELL`
<$SHELL> -c <command>
`$SHELL` captured at construction
DelegatingExecutor._shell
self._shell
`exit_code = -1` is overloaded
Timeout behavior is asymmetric
CommandTimedOut
*uncaught* Fabric timeout
`shlex.quote` on `cwd` only
RCAC_MCP_EOF
PATTERNS

# The new contract texts exist.
grep -q 'TIMEOUT_EXIT_CODE' src/rcac_mcp/executor/base.py || { echo "FAILED  base.py does not define TIMEOUT_EXIT_CODE"; status=1; }
uv run python - <<'PY' || status=1
import inspect, sys
from rcac_mcp.executor.base import Executor
doc = inspect.getdoc(Executor.run) or ''
missing = [w for w in ('argv', 'stdin', 'TIMEOUT_EXIT_CODE', 'NOT_STARTED_EXIT_CODE', 'bash', 'startup') if w not in doc]
if missing:
    print('FAILED  Executor.run docstring does not mention:', ', '.join(missing)); sys.exit(1)
PY
grep -q -- '-1001' AGENTS.md || { echo "FAILED  AGENTS.md does not document -1001"; status=1; }
grep -q -- '-1002' AGENTS.md || { echo "FAILED  AGENTS.md does not document -1002"; status=1; }
grep -q -- '/bin/bash -l' AGENTS.md || { echo "FAILED  AGENTS.md does not name /bin/bash -l"; status=1; }

# The whole mutation suite, plus the runner's own controls.
cases=$(uv run python -c "import sys; sys.path.insert(0, 'tests'); import mutants; print(' '.join(c.id for c in mutants.CASES))") || status=1
# shellcheck disable=SC2086
uv run python tests/mutants.py --self-check $cases || status=1

[ "$status" -eq 0 ] && echo "sweep: green"
exit "$status"
