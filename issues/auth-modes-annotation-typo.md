---
status: unshaped
kind: fix
appetite: small
lane: public
---

# AUTH_MODES carries an annotation that is accidentally a slice

## Problem

`src/rcac_mcp/auth.py:65`:

```python
AUTH_MODES: Final[Dict[str, Callable[[], AuthProvider]]:] = {
```

Note the `:` before the final `]`. Inside a subscript that is a **slice expression**, so the
annotation is `Final[slice(Dict[str, Callable[[], AuthProvider]], None)]` rather than the intended
`Final[Dict[str, Callable[[], AuthProvider]]]`.

It parses, and it never raises, purely because `from __future__ import annotations` (line 8) makes
module-level variable annotations strings that are never evaluated. Verified:

```
$ uv run python -c "import ast; ast.parse(open('src/rcac_mcp/auth.py').read()); print('parses OK')"
parses OK
$ uv run python -c "import rcac_mcp.auth as a; print(a.__annotations__['AUTH_MODES'])"
Final[Dict[str, Callable[[], AuthProvider]]:]
```

So the stored annotation is literally that string. Anything that evaluates it fails: `typing.get_type_hints`,
a runtime-validating framework introspecting the module, a documentation generator, a type checker
run for the first time — or a future Python that changes how deferred annotations are materialized.
That it is latent rather than live is luck, and the module it is latent in is the one that decides
whether a request is authenticated at all.

There is also a stray trailing blank line at the end of the file (line 70).

## Why it was deferred

Not deferred — found while auditing `auth.py` to write the invariant checklist, and filed rather than
fixed in place, because the harness port was not a cycle and a one-character edit to the auth module
is still an edit to the auth module.

**Pre-existing:** yes — on `main` as of `2def4a4`.

## Outcome / vision

The annotation says what it means, and something mechanical would have caught it. The one-character
fix is trivial; the interesting question the promotion should settle is whether this cycle also adds
the check that would have found it — no type checker or linter runs on this repository, and there is
no CI to run one in.

## Sketch of the acceptance criteria

- **R1** — The `AUTH_MODES` annotation SHALL be a valid type expression, and
  `typing.get_type_hints(rcac_mcp.auth)` SHALL succeed.
- **R2** — A test SHALL assert `get_type_hints` succeeds on the auth module, so the class of error
  cannot silently return.

## Notes

- Scope question for promotion: adding a type checker (or even `python -m compileall` plus a
  `get_type_hints` smoke test across every module) is a larger, generally useful change that belongs
  with [`ci-workflow.md`](ci-workflow.md) rather than being smuggled into a one-character fix. R2 as
  drafted is the small version — one module, one assertion. Decide deliberately.
- Note the same file's `create_auth_oidc()` re-imports `OIDCProxy` locally (line 44) though it is
  already imported at module scope (line 15), with a "lazy import for optional dependency" comment
  that the module-level import defeats. Harmless, but it is the same kind of unreviewed detail; the
  promotion may fold it in or leave it.
- Found by: the harness port, 2026-08-26 (out of cycle).
