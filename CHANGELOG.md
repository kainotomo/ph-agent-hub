# Changelog

All notable changes to PH Agent Hub are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- **Current session context** — the agent is told which conversation it is running in, so it can cite that conversation in its answers and in anything it writes downstream. A `## Current Session` block carrying the session id and canonical `{FRONTEND_URL}/chat/{id}` URL is injected into the system prompt directly after the platform identity, and admin templates may reference the running session with the `{{SESSION_ID}}` / `{{SESSION_URL}}` placeholders. Workflow step agents, which never receive the session system prompt, get the same block appended to their per-step instructions (as a config-only change, so checkpoint resumability is unaffected); resumed runs keep the same session id and URL. Every tool invocation is additionally seeded with `session_id` and `session_url` alongside `session_data`, so a writer tool can stamp provenance from `ctx.kwargs` without the model retyping the link. Sessions with no resolvable link — guest/widget and demo identities, or an unconfigured `FRONTEND_URL` — are reported as such rather than handed a URL that cannot open. ([#572])
- **MAF 1.19.0 Workflow Engine** — `workflow_based` skills now execute as multi-step, multi-model agent graphs via the MAF 1.19.0 Workflow API. Each step uses its own model configuration, progress streams as SSE `workflow_step` events, and per-step failures are reported without fallback to single-agent execution. Includes a two-step example workflow (`web_research_report`: research → report). ([#XXX])
- **Workflow Step Progress UI** — chat renders workflow step progress indicators (step name, status dot: started/completed/failed) inline in the chat view alongside other streaming events.
- **Process Steps Fold** — one collapsible fold per assistant response showing every step (reasoning, tool call, tool result) in exact order. Replaces the separate "Reasoning" and "Tool Activity" dropdowns. ([#538])
- **Bulk Move Chats** — select multiple chats in the sidebar and move them to a folder (or Unfiled) in one action, mirroring bulk delete. ([#526])
- **Chat Folders** — organise chat sessions into collapsible folders in the sidebar, with native drag & drop and a Move-to-folder menu. ([#526])
- **Parallel Tool Execution** — multiple independent tool calls run concurrently via `asyncio.gather`. Parallel batches count as a single step toward `AGENT_MAX_STEPS`. ([#447])
- **Self-Improving Agent** — agent learns from feedback, usage patterns, and outcomes to improve over time. ([#451])
- **Stream Resilience** — preserves partial response when user navigates away mid-stream; reconnects to live agent stream after page navigation. ([#455], [#457])
- **Agent Autopilot** — autonomous multi-turn execution without requiring user prompts. Supports pause, resume, and stop. Configurable turn and token limits for runaway protection. ([#446])
- **Background Tasks** — long-running agent execution with progress tracking, cancellation, and notifications. Runs independently of the user's current chat session. ([#449])
- **Scheduled & Recurring Tasks** — time-based autonomous agent execution with one-shot (datetime) and recurring (cron) schedules. Background polling loop checks for due tasks. ([#297])
- **Goal-Based Skills** — define objectives instead of prompts; the agent plans its own execution to achieve the goal. Skills now have a `goal_based` execution type. ([#448])
- **In-App Notifications** — persistent notification records for background task completion, scheduled task events, and other agent-triggered events. Bell icon with unread count badge. 
- **Auto Tool Selection** — LLM automatically selects relevant tools from the available pool. Configurable top-K (default 8) with random diversity sampling. ([#439])
- **Improved Sidebar** — redesigned sidebar navigation for better usability. ([#467])
- **Mobile Compatible Scheduled Tasks** — scheduled tasks view is now mobile-responsive. ([#466])
- **Mass Delete Chat Sessions** — admin can delete multiple chat sessions at once. ([#460])
- **Memory export, clear-all and merge** — `GET /memory/export` returns every entry
  (full values) as JSON, `DELETE /memory` clears the caller's memory, and
  `POST /memory/merge` folds near-duplicate entries into one. The chat Memory
  drawer gains **Export** and **Clear all** buttons plus a duplicate-key warning
  with a merge dialog. ([#569])
- **Memory growth policy** — new `MEMORY_MAX_ENTRIES_PER_USER` (default 500, 0
  disables) and `MEMORY_RETENTION_DAYS` (default 0 = disabled) settings bound the
  memory table. Pruning runs on every write path and only ever removes
  `source="automatic"` global entries; user-created entries are never deleted. ([#569])
- **Memory audit logging** — user-initiated memory create, update, delete, clear
  and merge now write audit-log rows (`memory.created`, `memory.updated`,
  `memory.deleted`, `memory.cleared`, `memory.merged`), matching the existing
  admin actions. Payloads never include the stored value. ([#569])

### Changed
- **In-Sidebar Session Search** — session search now filters the left sidebar in place
  instead of opening a separate results drawer. The magnifier icon toggles an inline
  search field (scope: All / Title / Content / Tag, plus the `#tag` exact-tag form).
  Results keep folder grouping: groups without matches are hidden, and the remaining
  groups are shown expanded. Filtered rows carry matched-field badges, and queries are
  debounced so the list updates
  as you type. The old right-side `SessionSearch` drawer was replaced by
  `SessionSearchBar` and the `useSessionSearch` hook.
- **Microsoft Agent Framework 1.12.1 → 1.19.0**, now pinned exactly in `backend/requirements.txt`
  (`agent-framework==1.19.0`). The dependency was previously unpinned, so image builds could silently
  resolve a different framework version than the one that was tested. `openai` is held at `>=2.25.0,<3`
  because `src/models/deepseek.py` relies on a deep OpenAI type path.
- Test suite result is unchanged from the pre-upgrade baseline (2 pre-existing failures, 1866 passed,
  5 skipped).
- **MCP behaviour changes** in the framework: server-initiated *sampling* requests are now denied by
  default, and framework-created MCP HTTP clients no longer persist response cookies. MCP servers
  configured in PH Agent Hub are unaffected (their HTTP client is caller-supplied).
- **Ordered reasoning segments** — assistant reasoning is now stored as ordered `reasoning` parts
  interleaved with tool events at the position it occurred, instead of one concatenated part at the
  front of the message content. No API, database, or UI change; existing messages remain valid. ([#537])
- **Session Usage Toolbar** — a session-level usage toolbar now renders below the message input with (a) session statistics — turns, steps, tok/s, with a popover showing LLM time, tool time, average TTFT, and TPS; (b) token usage — total, cache hit %, uncached input, cached input, and output; (c) the context ring moved from the chat top bar into that toolbar and its popover now shows a context breakdown (system prompt, tool definitions, messages); and (d) the per-response token chip was removed from assistant messages because it presented a truncated per-message count as a precise measure. ([#531])
- **Lighter chat history loading** — the chat view requests 10 messages per page instead of 50, so long conversations open with far less content in the browser. Older pages still load on scroll-up, and the API's `limit` contract (default 50, max 100) is unchanged. ([#536])
- **On-Demand Step Details** — the message list no longer ships reasoning and tool-result bodies. Bulky parts are collapsed to a short preview (`{ chars, summary }`) and the full body is fetched only when a step is expanded, keeping long thinking traces out of the initial payload. ([#539])
- **Workflow definition identity and edit policy** — `WorkflowDefinition.key` must match the module's `MAF_KEY` and is immutable (renaming is a create, not an edit); step `id` must be a non-empty, whitespace-free executor identity; and a new derived classification (`unchanged` / `config_only` / `topology`) states which edits leave paused runs resumable and which cannot be resumed. ([#551])

### Fixed
- **Workflow tool-role references** now resolve against the tenant's enabled tool **type** (the tools.type column) instead of the tool's display name, so the shipped web_research_report workflow runs for tenants that have Web Search enabled. ([#550])
- Search queries are now matched as a literal, case-insensitive substring of the selected field (e.g. `MC:xpar` returns only that session, `XXX:xpar` returns none). Wildcard characters (`%`, `_`) are matched literally. The stale FULLTEXT index `idx_sessions_title_ft` has been dropped. ([#541])
- Admin session deletion now works correctly. ([#460])
- Chat no longer auto-scrolls to the end when a response finishes while the user
  is reading earlier messages; a badge on the scroll-to-bottom button indicates
  the finished response. ([#521])
- Reasoning panel now stays collapsed by default during streaming and expands
  only when clicked, matching Tool Activity. ([#524])
- Context-window gauge is now accessible: visible percentage text, non-hue-only
  severity bands, and a warning icon at ≥75% usage. Relocated from the sidebar
  header to the chat top bar, hidden for unsent new chats. ([#530])
- **Relevance-ranked memory injection** — when a user has more global memory
  entries than `MEMORY_PROMPT_MAX_ENTRIES`, the injected entries are now ranked by
  embedding similarity to the current message instead of always being the newest
  ones. The candidate pool is bounded by the new
  `MEMORY_PROMPT_CANDIDATE_ENTRIES` (default 100) and can be disabled with
  `MEMORY_PROMPT_SEMANTIC_RANKING=false`. Below the limit the previous
  most-recently-updated ordering is kept, so no embeddings are computed for the
  common small-memory case. ([#569])
- **Memory value caps revisited** — the injected prompt block now truncates each
  value at 1000 characters (was 500) while the storage cap stays at 8000
  characters, and `list_memory` has its own self-bounded output
  (`MEMORY_TOOL_MAX_ENTRIES` = 100, `MEMORY_TOOL_MAX_CHARS` = 20000). A very large
  memory set can no longer be silently clipped by the runner tool-output cap: the
  tool reports `truncated`, `omitted` and a message instead. Full values remain
  available through the tool and the export endpoint. ([#569])
- **`/admin/memories` pagination is validated** — `page` must be ≥ 1 and
  `page_size` between 1 and 200, matching the other admin list endpoints. ([#569])
- **Admin memory list resolves user names** — the User column now shows the user's
  display name (falling back to their email, then a shortened id) instead of a raw
  UUID. ([#569])
- **`read_only` goal-based skills no longer write memory** — `save_memory` and
  `delete_memory` are stripped along with the other write tools, and the memory
  guidance block is omitted from the prompt so the model is not told to call a
  tool it no longer has. `list_memory` stays available. ([#569])
- **Temporary sessions no longer run cross-session retrieval** — temporary
  sessions are documented as leaving no trace, but the semantic retrieval of past
  conversation snippets still ran for them. It is now skipped, matching the
  persistent-memory block. ([#569])

---

## [1.25.1] — 2026-07-03

### Added
- **File upload session promotion** — temporary (guest/widget) messages are promoted to permanent sessions when a file is uploaded, preserving context. ([#445])
- **GitHub tool — expanded API coverage** — read/write capabilities for repositories, issues, and pull requests. ([#441])
- **GitHub personal account integration** — frontend UI for connecting personal GitHub accounts via OAuth. ([#434])
- **Auto tool selection improvements** — tools are now randomly loosened to improve selection diversity; ERPNext included in auto-select set. ([#439])
- **ERPNext per-user credentials** — users can connect their own ERPNext accounts via Account Settings, with tenant-level config as fallback. ([#432])

### Changed
- Version bumped to 1.25.1.
- `erpnext` added to auto-select tools list.
- Automatic tool callable filtering removed — all tools are available regardless of enabled state.

### Fixed
- Temporary session messages now persist correctly during file upload.
- GitHub token mocks updated in integration tests.

---

## [1.25.0] — 2026-07-03

### Added
- **GitHub tool — read/write API coverage** — full CRUD for repositories, issues, pull requests, and files. ([#441])
- **GitHub personal accounts** — frontend UI for OAuth-based personal GitHub account connection. ([#434])
- **Auto-select diversity** — random tool selection loosening for better coverage. ([#439])

---

## [1.24.3] — 2026-07-02

### Added
- `erpnext` included in auto-select tools list.

### Changed
- Version bumped to 1.24.3.

---

## [1.24.2] — 2026-07-02

### Changed
- Removed enabled-check for tool callables — all registered tools are now available to the agent regardless of their `enabled` flag state.
- Version bumped to 1.24.2.

---

## [1.24.1] — 2026-07-02

### Changed
- Enhanced ERPNext tool handling in modals.
- Version bumped to 1.24.1.

---

## [1.24.0] — 2026-07-02

### Added
- **Per-user ERPNext credentials** — users can connect their own ERPNext sites with distinct API keys/permissions via Account Settings. Tenant-level API key serves as fallback. ([#432], D-14)

---

## [1.23.0] — 2026-06-29

### Added
- **Stock Screener tool** — screen stocks using `yfinance` EquityQuery with configurable filters (market cap, sector, price, volume). ([#428])
- Stock Screener option added to ToolForm and ToolList UI.

---

## [1.22.5] — 2026-06-25

### Fixed
- **Mobile view hidden content** — hidden elements on mobile now display correctly. ([#422])
- **Android PWA installation** — resolved installation issues on Android Chrome. ([#421])

---

## [1.22.1] — 2026-06-22

### Added
- **A2A (Agent-to-Agent) Protocol** — full implementation of the Google A2A protocol (HTTP+JSON/REST binding). ([#404], [#406])
  - **A2A Server** — exposes ph-agent-hub agents as A2A-compatible agents via `/.well-known/agent-card.json` discovery. ([#404])
  - **A2A Client** — connect to external A2A agents as callable tools. ([#404])
  - **Task lifecycle** — `SUBMITTED → WORKING → COMPLETED/FAILED/CANCELED/INPUT_REQUIRED/AUTH_REQUIRED` state machine with Redis-backed cancellation and DB persistence. ([#411])
  - **INPUT_REQUIRED support** — agents can request user input mid-task; frontend displays inline prompts. ([#415], [#416])
  - **AUTH_REQUIRED support** — OAuth2 authentication trigger for task lifecycle. ([#417])
  - **Outbound OAuth2** — OAuth2 Authorization Code grant for connecting to remote agents. ([#418])
  - **A2A Call Logs** — admin UI page for viewing call history with pagination and filtering. ([#419])
  - **Resilience** — retry logic (configurable attempts, exponential backoff), timeouts (connect/read/stream), circuit breaker (configurable threshold/window/cooldown), observability via call logs. ([#409])
  - **Tool fidelity** — structured I/O, examples, and Part type support. ([#408])
  - **End-to-end tests** for INPUT_REQUIRED flow.
- **Frontend unit tests** — core chat components. ([#402])

### Fixed
- **Email Account Settings scrolling** — account settings page now scrolls properly. ([#405])
- **Auto model selection** — resolved issue where "Auto" model option could not be selected. ([#400])
- **Follow-up questions session validation** — follow-up question endpoint now properly validates session ownership. ([#395])
- **CI coverage threshold** — gradually raised `--cov-fail-under` for better quality enforcement. ([#381])

### Changed
- Version bumped to 1.22.1.
- A2A task records use DB-backed persistence with configurable TTL.

[#395]: https://github.com/kainotomo/ph-agent-hub/issues/395
[#398]: https://github.com/kainotomo/ph-agent-hub/issues/398
[#400]: https://github.com/kainotomo/ph-agent-hub/issues/400
[#402]: https://github.com/kainotomo/ph-agent-hub/issues/402
[#404]: https://github.com/kainotomo/ph-agent-hub/issues/404
[#405]: https://github.com/kainotomo/ph-agent-hub/issues/405
[#406]: https://github.com/kainotomo/ph-agent-hub/issues/406
[#408]: https://github.com/kainotomo/ph-agent-hub/issues/408
[#409]: https://github.com/kainotomo/ph-agent-hub/issues/409
[#411]: https://github.com/kainotomo/ph-agent-hub/issues/411
[#415]: https://github.com/kainotomo/ph-agent-hub/issues/415
[#416]: https://github.com/kainotomo/ph-agent-hub/issues/416
[#417]: https://github.com/kainotomo/ph-agent-hub/issues/417
[#418]: https://github.com/kainotomo/ph-agent-hub/issues/418
[#419]: https://github.com/kainotomo/ph-agent-hub/issues/419
[#421]: https://github.com/kainotomo/ph-agent-hub/issues/421
[#422]: https://github.com/kainotomo/ph-agent-hub/issues/422
[#428]: https://github.com/kainotomo/ph-agent-hub/issues/428
[#432]: https://github.com/kainotomo/ph-agent-hub/issues/432
[#434]: https://github.com/kainotomo/ph-agent-hub/issues/434
[#439]: https://github.com/kainotomo/ph-agent-hub/issues/439
[#441]: https://github.com/kainotomo/ph-agent-hub/issues/441
[#445]: https://github.com/kainotomo/ph-agent-hub/issues/445
[#446]: https://github.com/kainotomo/ph-agent-hub/issues/446
[#447]: https://github.com/kainotomo/ph-agent-hub/issues/447
[#448]: https://github.com/kainotomo/ph-agent-hub/issues/448
[#449]: https://github.com/kainotomo/ph-agent-hub/issues/449
[#451]: https://github.com/kainotomo/ph-agent-hub/issues/451
[#455]: https://github.com/kainotomo/ph-agent-hub/issues/455
[#457]: https://github.com/kainotomo/ph-agent-hub/issues/457
[#460]: https://github.com/kainotomo/ph-agent-hub/issues/460
[#466]: https://github.com/kainotomo/ph-agent-hub/issues/466
[#467]: https://github.com/kainotomo/ph-agent-hub/issues/467
[#521]: https://github.com/kainotomo/ph-agent-hub/issues/521
[#524]: https://github.com/kainotomo/ph-agent-hub/issues/524
[#526]: https://github.com/kainotomo/ph-agent-hub/issues/526
[#530]: https://github.com/kainotomo/ph-agent-hub/issues/530
[#531]: https://github.com/kainotomo/ph-agent-hub/issues/531
[#536]: https://github.com/kainotomo/ph-agent-hub/issues/536
[#539]: https://github.com/kainotomo/ph-agent-hub/issues/539
[#541]: https://github.com/kainotomo/ph-agent-hub/issues/541
