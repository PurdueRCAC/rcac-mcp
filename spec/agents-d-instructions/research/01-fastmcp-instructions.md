# R01 — FastMCP 4.1 / SDK 2.3: instructions, discover caching, resource and tool paths

Paths: `fm/` = `.venv/lib/python3.14/site-packages/fastmcp`, `mcp/` and `mcp_types/` likewise.
Claims marked **(probed)** were checked on 2026-10-19 by a scratch script against the installed
stack (in-memory client in both eras, and in-process streamable HTTP with real JWTs). Nothing in
the repo was changed.

## 1. How instructions are served

- **Storage.** `FastMCP(instructions=...)` (`fm/server/server.py:323`) is passed unchanged to
  `LowLevelServer(...)` (`:455`), which stores it as `self.instructions` (`mcp/server/lowlevel/server.py:424`).
  `FastMCP.instructions` is a read/write property over `_mcp_server.instructions`
  (`fm/server/server.py:549-555`).
- **Handshake era.** `create_initialization_options()` copies `self.instructions` into
  `InitializationOptions` (`mcp/server/lowlevel/server.py:562`); `ServerRunner._handle_initialize`
  puts `opts.instructions` into `InitializeResult.instructions` (`mcp/server/runner.py:429-444`).
- **2026-07-28.** The default `server/discover` handler (`mcp/server/lowlevel/server.py:460`,
  `:672-688`) builds `DiscoverResult(instructions=self.instructions)` at call time. FastMCP routes
  it through its middleware `on_discover` hook (`fm/server/low_level.py:194-195`, `:322-375`) but
  does not touch the field.
- **Verbatim.** No strip, dedent, length cap or normalisation was found in the server
  (`lowlevel/server.py`, `runner.py`, `fm/server/low_level.py`) or the client
  (`mcp/client/session.py:839-844` returns the field as is). **(probed)** Leading or trailing
  whitespace and tabs round-trip byte-identical in both eras, and so does a 20,000-character string.
  The 4,096 cut is applied by Claude Code, not by the stack.
- **When it is read.** It is read on each request, not once at construction, but the
  handshake path depends on when the transport takes its `InitializationOptions` snapshot:
  - stdio: once, when `run_stdio_async` starts (`fm/server/mixins/transport.py:245-249`);
  - SSE: once per SSE connection (`fm/server/http.py:524-528`);
  - streamable HTTP: no snapshot. `init_options` is None, so each `initialize` calls
    `create_initialization_options()` again (`mcp/server/runner.py:431`; the session manager
    passes none, `mcp/server/streamable_http_manager.py:245-247`, `:357-363`);
  - in-memory (the harness): once per connection (`fm/client/transports/memory.py:90-95`);
  - discover: every request (`lowlevel/server.py:686`).
  **(probed)** Setting `server.instructions = 'SET-AFTER'` after construction reached the next
  connection in both eras. A value set after `mcp.run(transport='stdio')` has begun would still
  reach `server/discover` but not `initialize`. Setting it before `run`, or passing it to the
  constructor, reaches every transport and both eras.

## 2. DiscoverResult caching

- Typed model: `DiscoverResult(CacheableResult)` (`mcp_types/_types.py:593`).
  `CacheableResult` defaults to `ttl_ms=0`, `cache_scope="private"` (`_types.py:198-213`). On the
  2026-07-28 wire both are required (`mcp_types/_v2026_07_28/__init__.py:2356`, `ttlMs` near
  `:2395`). **(probed)** By default a modern discover carries `ttlMs=0, cacheScope='private'`.
- Supported override: `FastMCP(cache_ttl=<s>, cache_scope='public'|'private')`
  (`fm/server/server.py:341-342`, `:429`, `:459`; `fm/server/caching.py`). The hint applies
  **uniformly** to every cacheable method: `prompts/list`, `resources/list`, `resources/read`,
  `resources/templates/list`, `server/discover` and `tools/list` (`mcp_types/methods.py:411-418`).
  The runner applies it after the handler, and only to fields the handler left unset
  (`mcp/server/runner.py:357-361`, `mcp/server/caching.py:45-58`). **(probed)** With
  `cache_ttl=60, cache_scope='public'`, discover carries `60000/public`. A narrower route: an
  `on_discover` middleware (`fm/server/middleware/middleware.py:232`) that returns a result with
  `ttl_ms`/`cache_scope` set. The hint would leave those fields alone (inferred from
  `apply_cache_hint`, not probed).
- The default is fine for host-wide text. `0/private` only means clients re-fetch, and the text is
  identical for everyone anyway. A server-wide `public` hint would also mark `resources/read` of
  the per-user `rcac://storage` as shareable, which is a reason **not** to set `cache_scope`
  globally. No change is needed.

## 3. Resource reads, middleware and modes

- Dispatch: `resources/read` → `on_read_resource`, inside `on_request`/`on_message`
  (`fm/server/middleware/middleware.py:177-178`, `:195-200`). All three repo middlewares
  override only `on_call_tool` (`src/rcac_mcp/middleware.py:44`, `:74`, `:146`), so a resource
  read passes through them untouched. No executor is set and no identity is checked.
- **(probed)** A handler that never calls `get_executor()` returned its text through
  `create_mcp_server` in bare, shared (with an executor that raises if run), delegate in-memory
  (no token), and delegate over HTTP with a mapped **and with an unmapped** JWT identity, in both
  eras. The current `rcac://context` fails everywhere with `No executor configured`.
- Delegate over HTTP **without** a token: the `/mcp` route is wrapped in `RequireAuthMiddleware`
  (`fm/server/http.py:699-705`), so the connect gets a 401 (probed `[401]` legacy,
  `[401, 401]` modern). Nothing reaches any handler, the instructions included.
- **Unmapped identity:** any token that passes `JWTVerifier` gets the instructions and can read the
  resource. Only tool calls check the user map. This is consistent with R9's "every
  authenticated user", but it widens access for `cluster_context` to callers who could not run a
  tool.
- A sync handler runs in a thread pool (`fm/resources/function_resource.py:211`). That is
  harmless for a handler that returns a module-level string. MIME: a `str` result gets
  `text/plain` (`fm/resources/base.py:72-78`, `function_resource.py:194`). **(probed)** A
  handler returning `''` succeeds in both eras with one content item `text=''`,
  `mimeType='text/plain'`.

## 4. A tool that touches no executor

- Delegate mode: `AuthExecutorMiddleware.on_call_tool` runs **before** the tool body. It
  raises `RuntimeError` when no identity claim is present (`middleware.py:163-167`) and
  `PermissionError` when the identity is unmapped (`:170-174`). `ToolErrorMiddleware` turns
  either into an error result (`:44-51`). With a mapped identity it builds `DelegatingExecutor`,
  whose constructor runs nothing (`executor/delegate.py:66-82`), and calls the tool. With no token
  in memory it passes straight through (`:156-159`).
  **(probed)** The `-> str` tool answered for `alice` (mapped) and for tokenless in-memory
  callers. For `mallory` (unmapped) it gave `is_error=True` with
  `"User 'mallory' is not authorized. Contact administrator to request access."`. So
  `cluster_context` cannot answer an unmapped identity, while `rcac://context` and the
  instructions can. R6 says "in any execution mode", not "any identity", so this is probably
  acceptable, but the plan should say it.
- Wire shape of a `-> str` tool **(probed)**: output schema
  `{"type":"object","properties":{"result":{"type":"string"}},"required":["result"],"x-fastmcp-wrap-result":true}`
  (`fm/tools/function_parsing.py:534`). A `''` return gives `content=[TextContent(text='')]`,
  `structuredContent={"result": ""}`, `is_error=False` (`fm/tools/base.py:425-429`).
  `harness.call` joins the text parts, so it yields `Outcome(False, '')`.

## 5. Harness surface

- Builders (`tests/harness.py:597-616`) all call `create_mcp_server(auth_mode, middlewares=[...])`
  positionally or by keyword, with no other argument: `delegate_server` →
  `('jwt', middlewares=[AuthExecutorMiddleware('jwt', map_path), *extra, MethodLog()])`;
  `shared_server` → `('none', middlewares=[SharedExecutorMiddleware(executor), *extra, MethodLog()])`;
  `bare_server` → `('none', middlewares=[*extra, MethodLog()])`. `served_instructions`,
  `served_surface` and `negotiate` take an already-built `FastMCP`, read
  `client.instructions` (`:350-362`, `:365-389`, `:407-430`) and are era-neutral. They need no new
  verb to serve custom instructions, **provided** the caller can build the server with injected
  site text (a new `create_mcp_server` parameter, or a context object the builders forward).
  Today the builders have no way to forward one.
- **Missing verb:** the harness has no resource-read helper. `served_surface` lists resources
  but never reads them, and nothing in `tests/` calls `read_resource`. R7 tests need one, for
  example `read_resource(client, uri) -> (text, mime)`. Its FastMCP import belongs in
  `harness.py` (`test_hygiene.py` forbids FastMCP imports elsewhere).
- **Other tests that call `create_mcp_server` directly:** `test_instructions.py:76`,
  `test_registry.py:124` and `test_surface.py:218` call `create_mcp_server('none')`.
  `test_exec_mode.py:161` stubs it with the signature `(auth_mode=None, middlewares=None)`.
  `test_surface.py:104-111` fails on `instructions differ` and `tool added`, and compares the
  instructions to `tests/fixtures/served_surface.json`, the frozen FastMCP-2 snapshot. Composed
  instructions and the new `cluster_context` tool both break
  `test_served_surface_matches_baseline` (and the `instructions-dropped` mutant,
  `tests/mutants.py:456-468`). `test_instructions.py:88-91` checks that every snake_case or
  backticked name in the served instructions is a registered tool, so site text containing such
  names would fail it.

## 6. Era and transport differences

- None in content: both eras read the same `self.instructions` attribute. The only divergence is
  timing: stdio and SSE snapshot `InitializationOptions` per run or per connection, while discover
  reads live (see §1).
- SSE is legacy-only on the client (`fm/client/transports/sse.py:38`), so over SSE only
  `initialize` carries the instructions. Modern runs over streamable HTTP and in-memory.
- No transport alters the string (§1).

## Implications for the plan

1. Compose the instructions **before** `FastMCP(...)` is constructed, or at least before `run()`,
   and pass them as `instructions=`. A post-construction setter works, but setting it after stdio
   `run` starts would split the eras. Do the startup read before `create_mcp_server` and hand the
   result in.
2. Make the site text injectable into `create_mcp_server` (for example a `site_context=`
   parameter) so the harness builders can forward a stub and no test reads the host's real
   `/etc/agents.d`. Extend `delegate_server`/`shared_server`/`bare_server` and the
   `test_exec_mode.py:161` stub to match.
3. Leave `cache_ttl`/`cache_scope` unset. The default `0/private` is correct, and a global
   `public` would mislabel `rcac://storage`.
4. The `rcac://context` handler can be a closure over the startup string. It works in every mode
   and era with the current middlewares and needs no middleware change. A `''` read is valid.
5. `cluster_context` (`-> str`) answers in every mode for mapped and tokenless callers. Unmapped
   identities get the `not authorized` error from `AuthExecutorMiddleware`. Either accept and
   document this, or exempt the tool in the middleware; the GOAL does not require it. Its
   structured result is `{"result": text}`.
6. Add a harness `read_resource` verb (both eras, in-memory and HTTP-with-token) for R7/R9.
7. Plan the `test_surface` baseline change: the instructions and the tool set both change by
   design, so the snapshot comparison needs an explicit, documented exemption or rebase. Also
   guard `test_instructions` against site-text identifiers, for example by checking only the
   base part.
8. The probe showed no length cap or normalisation, so R4's budget must be enforced entirely by
   the server, and R3 byte-identity holds end to end.
