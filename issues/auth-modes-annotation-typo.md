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

It parses, it never raises, and — this is the part that matters — **evaluating it does not raise
either.** It silently resolves to a `Final` parameterized by a `slice` object:

```
$ uv run python -c "import typing, rcac_mcp.auth as a; \
    h = typing.get_type_hints(a)['AUTH_MODES']; print(h); \
    print('arg[0] is a slice:', isinstance(typing.get_args(h)[0], slice))"
typing.Final[slice(typing.Dict[str, typing.Callable[[], ...AuthProvider]], None, None)]
arg[0] is a slice: True
```

Two intuitions to discard, both verified false on Python 3.14.2 — the only version this project
supports:

- **`typing.get_type_hints` does not fail on it.** It succeeds and hands back the nonsense type.
- **`from __future__ import annotations` (line 8) is not what saves it.** The same expression
  evaluated eagerly, with no deferral at all, also yields `Final[slice(...)]` without complaint —
  `typing` no longer rejects a non-type argument here.

That makes this worse than a latent error, not better. A loud failure would be caught by the first
tool that introspected the module; a silent one means every consumer of the annotation — a type
checker, a doc generator, a runtime validator — sees a `slice` where a mapping type should be, and
the module it happens in is the one that decides whether a request is authenticated at all. It also
means **any check that merely asserts evaluation succeeds passes on the broken code**, which is the
trap the acceptance criteria below have to avoid.

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

**Do not write a criterion that only asserts evaluation succeeds — it already does.** The criterion
has to assert the *shape* of the resolved annotation, or it goes green on the unfixed code.

- **R1** — The `AUTH_MODES` annotation SHALL be a valid type expression: WHEN
  `typing.get_type_hints(rcac_mcp.auth)['AUTH_MODES']` is resolved, its argument SHALL be the mapping
  type and SHALL NOT be a `slice`.
- **R2** — A test SHALL assert exactly that, and SHALL be demonstrated to FAIL against the unfixed
  annotation before the fix lands — a criterion for a silent defect is worthless until it has been
  seen red.

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
