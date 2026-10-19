# 02 — Client truncation of server `instructions`

Retrieved 2026-10-19. Installed Claude Code for the binary checks: v2.1.303.

## 1. Claude Code

**Documented limits.** The source is code.claude.com/docs/en/mcp, section "Tool search for MCP server authors" (anchor `#for-mcp-server-authors`). It gives two limits:
- **4,096 characters** for "Server instructions, and the description of a tool that loads upfront".
- **16,384 characters** for a tool description that Claude loads through tool search.

The docs also say "Claude sees only the text before the cut" and advise putting critical details near the start. A tool loaded upfront (tool search off, `alwaysLoad`, or a server exempt from deferral) keeps the 4,096 limit.

**`CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH`.** The variable is documented at code.claude.com/docs/en/env-vars:
- It takes a positive whole number in plain digits; any other value is ignored and the defaults apply.
- It "replaces both default limits", so setting `8192` raises the 4,096 limit and lowers the 16,384 one.
- It requires v2.1.280 or later.
- Users set it in the shell or in the `env` block of `settings.json`. The MCP page points to the settings.json `env` field for the related `ENABLE_TOOL_SEARCH`, and both are ordinary env vars.

Docs exemption: descriptions and instructions from "some of the MCP servers that Anthropic hosts at `api.anthropic.com`" reach Claude in full. The binary confirms this for http/sse first-party URLs, where the limit is `Infinity`.

**Changelog history** (github.com/anthropics/claude-code/blob/main/CHANGELOG.md):

| Version | Change |
|---|---|
| 1.0.52 | "Added support for MCP server instructions" |
| 2.1.84 | Descriptions and instructions "now capped at 2KB to prevent OpenAPI-generated servers from bloating context" |
| 2.1.280 | Added `CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH` "to change the 2,048-character cap" |
| 2.1.283 | `/context` now counts MCP server instructions as their own row |
| 2.1.295 | Descriptions loaded through tool search are cut at 16,384 instead of 2,048 |
| 2.1.296 | "Changed the default limit on MCP tool descriptions sent up front and on MCP server instructions from 2,048 to 4,096 characters" |
| 2.1.299 | First-party `api.anthropic.com` servers are no longer cut at 4,096 |

**Silent or marked: marked.** The docs don't say. The v2.1.303 binary shows that the cut is marked:
- A small function compares the instructions with the limit: `if(e.length<=s)return e; … return ne(e,s)+"… [truncated]"`.
- It writes a debug-log line, "Server instructions truncated from X to Y chars".
- The model therefore sees up to the limit plus the 14-character suffix `… [truncated]`.

This session shows the same thing live. The Scite connector's instructions, delivered through Claude Code, end in `citatio… [truncated]`.

**Unit: UTF-16 code units (JavaScript `.length`).** The docs say only "characters", and the unit was confirmed from the binary:
- The comparison uses `String.length`, which counts UTF-16 code units.
- The cutting helper appears to be surrogate-safe: it slices `n` code units and drops a trailing high surrogate.

So every BMP character, including `é`, `—` and CJK, counts 1, and an astral character (emoji, U+1F600) counts 2. This is not a byte count. The text is not NFC-normalized before measuring. Caveat: this was read from minified code and is not a documented contract.

**What wraps the instructions, and whether it counts.**
- Instructions reach the model as a meta message: `# MCP Server Instructions` / "The following MCP servers have provided instructions…", then `## <server name>`, then the text.
- The limit applies to the raw `instructions` string before this wrapper is added, so the header and the server name don't count.
- Instructions load at session start with tool search on (docs: "Only tool names and server instructions load at session start").
- With tool search off they are still injected. The injection code path seen in the binary is not gated on tool search, but the docs state this explicitly only for the tool-search-on case.
- 2.1.300 fixed a late-connecting server's instructions not reaching Claude until its next tool call.

## 2. Other clients

| Client | Passes `instructions` to the model? | Truncation | Source |
|---|---|---|---|
| Claude Desktop / claude.ai connectors | Not documented. The connector-building page lists limits for tool results and timeouts only. | unknown | claude.com/docs/connectors/building |
| OpenAI Codex CLI | Yes. Used as the tool-search **namespace description** for the server's tools (`namespace_description: server_instructions`). Also part of the tool-catalog cache key. | None in `rmcp_client.rs`; elsewhere unknown | openai/codex `codex-rs/codex-mcp/src/rmcp_client.rs` (main) |
| VS Code + Copilot | Listed as supported ("Server instructions"). Stored with cached server metadata (`serverInstructions` in `mcpServer.ts`). How it reaches the prompt is not documented. | unknown | code.visualstudio.com/docs/copilot/guides/mcp-developer-guide; microsoft/vscode `src/vs/workbench/contrib/mcp/common/mcpServer.ts` |
| Cursor | No source found | unknown | forum and docs searches returned nothing |
| Gemini CLI | Yes. Wrapped as "instructions provided by the tool server '<name>'" between `---[start/end of server instructions]---` markers and appended to project memory (GEMINI.md context). Only in **trusted folders**. | None | google-gemini/gemini-cli `packages/core/src/tools/mcp-client-manager.ts` `getMcpInstructions`; `context/memoryContextManager.ts` (main, 2026-10-16) |
| Warp | Its MCP docs page doesn't mention instructions | unknown | docs.warp.dev/agents/capabilities/mcp.md |
| Goose | Yes. Rendered into the system prompt under `## <extension>` / `### Instructions`, with `{{WORKING_DIR}}` substituted. | None | block/goose `crates/goose/src/prompts/system.md`, `agents/mcp_client.rs` (main, 2026-10-19) |
| Zed | **No.** Its `InitializeResponse` struct has no `instructions` field, so the field is dropped when the response is parsed. | n/a | zed-industries/zed `crates/context_server/src/types.rs` (main, 2026-10-17) |

Claude Code is the only client found that sets a hard limit.

## 3. MCP spec

`DiscoverResult` in schema revision 2026-07-28 (modelcontextprotocol.io/specification/2026-07-28/schema) carries `instructions?: string`. The spec describes it as natural-language guidance that clients may use "by including it in a system prompt", and says it "should not duplicate information already in tool descriptions". The spec sets **no length limit**.

The same result carries the `CacheableResult` fields:
- `ttlMs` is required. It works like HTTP `max-age`: `0` means immediately stale, and a positive value means fresh for that many milliseconds.
- `cacheScope` is `"public"` or `"private"`. With `public`, any client or intermediary may cache the response across auth contexts. With `private`, a cache "MUST NOT be shared across authorization contexts".

Composed instructions therefore get cached under whatever `ttlMs`/`cacheScope` `server/discover` returns. If the text varies per user or site, use `private`. The MCP blog post "Server Instructions: Giving LLMs a user manual for your server" (2025-11-03, blog.modelcontextprotocol.io) advises "concise and actionable" text and "Don't write a manual", but gives no number. The official clients matrix page now redirects to the intro, so no per-client support table is available.

## Implications for the plan

- **Default budget: 4,096, counted exactly as Claude Code counts.** Measure with UTF-16 code units: in Python, `len(s.encode("utf-16-le")) // 2`, not `len(s)`, which counts code points. The two agree unless the text contains astral characters.
- **Prefer ASCII, or keep a safety margin.** Python `len()` undercounts emoji. Either compute the UTF-16 length or forbid astral characters in rules.
- **Never rely on the tail.** Put the highest-priority rules first so that a cut in Claude Code before v2.1.296 (2,048) still keeps the essentials. Consider a priority order that fits the most important rules within 2,048. The `… [truncated]` suffix is appended outside the budget, so no reserve is needed for it.
- **Install docs should tell users three things:**
  - Claude Code before v2.1.296 cuts at 2,048, so upgrade.
  - To raise the limit, set `CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH` (v2.1.280 or later) in the shell or in `settings.json` → `env`. This also lowers the 16,384 limit for deferred tool descriptions, so 16384 is the safe value if a raise is wanted.
  - `/context` shows the instructions row (v2.1.283 or later), and the debug log reports truncation.
- **Other clients:**
  - Gemini CLI injects instructions only in trusted folders.
  - Codex uses them as a tool-namespace description, so the opening sentence should say what the tools are for.
  - Zed drops them entirely, so any critical rule must also be enforced server-side or appear in tool descriptions.
- **Discover caching:** if instructions are composed per site or per user, return `cacheScope: "private"` and a modest `ttlMs` so that rule edits propagate.
