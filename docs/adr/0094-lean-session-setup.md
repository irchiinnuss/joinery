# 0094. Lean session setup: own tools only, effort per step, fewer round trips

Status: accepted (2026-11-23)

## Context

A token profile of Yard Day's Journeyman sessions (244 Claude Code transcripts,
1,977+ tool calls, all on the strong model at high effort) found waste in how a
session is set up rather than in what it does:

- Every Joinery Claude session loaded the user's claude.ai account connectors:
  about 77 tools (Gmail, Calendar, Drive, Sheets, Slides, Docs, Data, and
  generative services such as ElevenLabs and Higgsfield). That is a privacy hole
  (the game's agent could read the user's mail and files) and a Rule Zero hole,
  and their names or schemas rode on every call. Codex sessions likewise loaded
  the user's own MCP servers (a pixel-art service, desktop control), the ChatGPT
  apps and plugins, and Codex's built-in image generator; none of those passes
  the hook's matcher (`mcp__.*|Bash`).
- Read-only roles were offered no ToolSearch, so Claude Code sent every MCP
  schema up front: a reviewer's first call measured 76.7k tokens.
- `Agent` was offered to every writing session and never called.
- About half the calls carried one bookkeeping tool: `todo_step` started/done, a
  ToolSearch loading the same eight registry tools, repeated
  `discover_tools`/`tool_schema`.
- Every to-do step ran at the user's high effort, wiring a signal as much as
  modelling a prop.

## Decision

1. **Joinery's own MCP server only.** Claude Code runs with
   `--strict-mcp-config` and `ENABLE_CLAUDEAI_MCP_SERVERS=false` (both: either
   alone drops the claude.ai connectors, checked with a real `claude -p`; the
   flag also drops user and project servers). Codex runs with
   `mcp_servers.<name>.enabled=false` for every server the user's
   (`$CODEX_HOME/config.toml`) or project's Codex config names, and with
   `features.{apps,plugins,remote_plugin,image_generation,browser_use,
   browser_use_external,in_app_browser,computer_use}=false`. Web reading stays.
   Gemini CLI and opencode receive Joinery's server over ACP; whether their own
   global MCP config also loads is not changed here (not verified on this
   machine: Gemini CLI is not installed, opencode 2 has no MCP listing).
2. **Deferred schemas everywhere.** Read-only roles get ToolSearch with their
   read tools, so Claude Code defers MCP schemas as it does for writers.
3. **Core tools preloaded.** The registry marks the tools nearly every turn calls
   (`discover_tools`, `read_tool`, `call_tool`, `search_project`, `details`,
   `todo_step`, `precheck`, `submit_for_review`, and the reviewer's and
   debugger's report tool) with MCP `_meta` `anthropic/alwaysLoad`. Claude Code
   sends them up front; the big, rarer schemas (blockout, edit_scene,
   propose_todo) stay deferred. Other agents ignore the mark; each role still
   lists only its own tools. The mark is part of the registry's tool list, so
   the cached prefix stays the same bytes from call to call.
4. **No Agent tool** for a writing Claude session. Joinery runs its own tasks.
5. **Effort per step.** A to-do built a step per prompt caps a routine step at
   `medium` (`routing.step_effort_cap`, carried as the prompt's
   `Selection.effort_cap` and applied by `models.route`, so every provider
   behaves the same). A step keeps the request's effort when it names a model,
   art or sound or the tool that makes it (Blender, Krita, Material Maker, Surge
   XT, SuperCollider, MIDI) or debugging. Never capped: the request itself and
   its plan, every fix after Joinery's checks, and the prompt that asks a step
   again. A cap only lowers, and only to an effort the model offers. Choosing
   Thorough still means high for the plan and the creative steps; its routine
   steps run at medium too. The timing line under a reply lists each effort the
   turn used in order ("High, Medium"), so the user sees it.
6. **Joinery keeps the step's books.** Built a step per prompt, Joinery marks a
   step started itself before its prompt and, when the agent ends a step that is
   not the last, done with the last line of the agent's reply as its note. The
   asset gate still runs on that done (`todo_step_checked`); when it refuses, the
   agent gets one prompt to fix what it left. An explicit `todo_step` still works
   (a skip, or the agent's own note). Each step's prompt lists up to six project
   tools its own words point to, with their arguments (the gateway's
   `discover_tools` search, `done_when.step_tools`), so the agent needs no
   `discover_tools` or `tool_schema` first.

## Not changed: the project's CLAUDE.md

Yard Day's `CLAUDE.md` carries a 4.5k-character handoff below Joinery's stub,
loaded into every Journeyman call via `--setting-sources project,local`. Claude
Code could skip it (`CLAUDE_CODE_DISABLE_CLAUDE_MDS`, or `claudeMdExcludes`), but
Joinery writes that stub as "yours to edit" (ADR 0077): what the user adds there
is their instruction, and dropping it silently would be wrong. Doing it for
Claude alone would also break parity: Codex and opencode read `AGENTS.md`,
Gemini CLI `GEMINI.md`. It stays loaded for every agent; the cost is about 1.2k
cached tokens per call. Moving a handoff into `JOURNEYMAN.md` (or out of the
project) is the user's call.

## Measurement

Real `claude -p` runs (Claude Code 2.1.327, haiku, the same flags Joinery
passes, without Joinery's appended instructions):

| Session | First call before | After |
| --- | --- | --- |
| Reviewer (read-only) | 76,658 tokens, 77 claude.ai tools | 10,268, none |
| Lead (writer) | 16,044, 77 claude.ai names, core tools behind ToolSearch | 11,349, none, core tools loaded |

Before, the lead's next call was almost always a ToolSearch for the core tools
(207 ToolSearch calls in the profile). Codex, before: `image_gen`,
`mcp__codex_apps__*`, `mcp__node_repl*` and the user's docs server; after: none.

## Consequences

- A Joinery session can no longer reach the user's mail, files, calendar or
  account apps, whichever agent runs it.
- Routine steps think less and cost less; when one turns out harder than it
  read, the checks catch it and the fix runs at the request's effort.
- A step's note is the agent's own last line; a reply that ends badly worded
  makes a poorer note for the next step than a deliberate `todo_step` call.
- Enforcement is unchanged and still shared (ADR 0006): the hook judges every
  call it did before; what changed is what each session is offered.
