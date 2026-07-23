# Changelog

All notable changes to PH Agent Hub are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
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

### Fixed
- Admin session deletion now works correctly. ([#460])

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
