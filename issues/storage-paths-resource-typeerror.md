---
status: unshaped
kind: fix
appetite: small
lane: public
---

# The rcac://storage resource raises TypeError on every read

## Problem

`resources.py:151` calls `storage_paths()`:

```python
from rcac_mcp.tools.rcac import storage_paths
paths = storage_paths()
```

But `storage_paths` is decorated with `@mcp_tool`, and that decorator does not return the function —
it returns `Tool.from_function(func)`, a `fastmcp.tools.tool.FunctionTool`. `FunctionTool` is not
callable, so the resource raises on every read.

Reproduced directly:

```
$ uv run python -c "from rcac_mcp.tools import rcac; rcac.storage_paths()"
storage_paths type: <class 'fastmcp.tools.tool.FunctionTool'>
callable? False
calling it -> TypeError: 'FunctionTool' object is not callable
```

The `rcac://storage` resource is registered (`resources.py:172`) and advertised in
`SERVER_INSTRUCTIONS` ("Resources: … `rcac://storage`: User's resolved storage paths"), so a client
that reads it gets an error for a capability the server claims to have. No test covers it — the whole
suite is documentation tests.

The `# Import here to avoid circular dependency` comment at `resources.py:148` suggests the import was
scrutinized; the fact that the imported object was no longer a function was not.

## Why it was deferred

Not deferred — found while writing the invariant checklist and filed rather than fixed on the spot,
because the harness port was not a cycle and should not have carried a product fix. The general lesson
*was* recorded immediately, as an invariant in `AGENTS.md` § *Tools* and
`.agents/factory/invariants.md` §7: a `@mcp_tool`-decorated function cannot be called from Python;
factor shared logic into an undecorated helper and have both call it.

**Pre-existing:** yes — on `main` as of `2def4a4`.

## Outcome / vision

Reading `rcac://storage` returns the formatted storage summary. The fix is the shape the invariant
already prescribes: an undecorated `_storage_paths()` helper holding the logic, with the `@mcp_tool`
wrapper and the resource both calling it. A test asserts the resource handler actually executes, so
this cannot silently regress.

## Sketch of the acceptance criteria

- **R1** — WHEN the `rcac://storage` resource handler is invoked, it SHALL return the formatted
  storage summary rather than raising `TypeError`.
- **R2** — The `storage_paths` MCP tool SHALL remain registered in `TOOL_REGISTRY` with its current
  name and schema.
- **R3** — A test SHALL invoke the resource handler and assert it returns a value — the missing check
  that let this ship.
- **R4** — IF any other module calls a `@mcp_tool`-decorated function directly, THEN that call site
  SHALL be fixed in the same cycle. (Grep for imports from `rcac_mcp.tools.*` outside the tools
  package; at filing time `resources.py` is the only one.)

## Notes

- Fixing this creates the undecorated seam that
  [`generalize-storage-discovery.md`](generalize-storage-discovery.md) needs, so doing the small fix
  first makes the larger cycle cheaper.
- Per `.agents/factory/ears.md`, these criteria are phrased as observable broken→fixed behavior. The
  mechanism above is evidence, not contract — `/cm-plan` confirms the root cause.
- Found by: the harness port, 2026-08-26 (out of cycle).
