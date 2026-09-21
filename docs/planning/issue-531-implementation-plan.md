# Issue #531 — DSH-Parity Session Usage Toolbar, Context Breakdown, and Per-Response Chip Removal

**Status:** approved plan, not yet implemented. Nothing in the repo has been modified.
**Repo:** `/home/phalo/AI/ph-agent-hub` · **Branch:** `issue-531` (already checked out)
**Pre-existing working-tree changes (leave alone):** `backend/src/main.py` and `frontend/package.json` both bumped to `2.6.0`.

---

## 0. Instructions to the executing agent

You are the orchestrator for this plan. Delegate each task below to a **local, free** subagent to minimise cost.

- **Subagent route:** `provider: freetoken`, `model: qwen3.8-35b-a3b-distill` (local, free).
- **Verify the route first.** A previous session was denied with `child LLM route "..." is not allowed for this Session`. Before launching the whole batch, send one throwaway probe:
  > `Reply with exactly: PROBE_OK` — with `provider: freetoken`, `model: qwen3.8-35b-a3b-distill`, `run_in_background: false`.
  If that errors, call `list_subagent_models` (and `list_subagent_models` with `provider: "freetoken"`) to get the exact permitted id, and use that instead. Do not proceed until the probe succeeds.
- **Cost rules:**
  1. Paste the **§4 shared preamble** once at the top of every task prompt, then the task block. Never repeat the §3 contract inside individual prompts — the task blocks are self-contained enough.
  2. Launch every task in a wave **in parallel in one message** (files are disjoint within a wave — no merge conflicts).
  3. Never let a subagent explore the repo beyond the files it needs. The task blocks name the exact files and line numbers.
  4. Use `run_in_background: true` (the default) and continue to the next wave's prep while they run; collect results as they settle.
- **Verification rule:** after each wave, run the cheap check yourself (grep/tsc/vitest/pytest) rather than trusting the subagent's summary. If a subagent fails its `Done-when`, re-delegate once with the failure output appended, then fix it yourself if it fails again — rework is cheaper than an unbounded retry loop.

### Wave / dependency map (files are disjoint within each wave)

| Wave | Tasks launched together | Notes |
|---|---|---|
| 1 | T0, T1, T2, T3, T6 | All zero-dependency |
| 2 | T4, T5, T7, T8, T10 | T4/T5 need T0+T1; T7 needs T3; T8 needs T2; T10 needs T1 |
| 3 | T9, T11 | T9 needs T4+T5 |
| 4 | T12 | Needs T9 |
| 5 | T13, T14 | Verification only |

Critical path: T0/T1 → T4/T5 → T9 → T12 → T13.

---

## 1. Goal

Replicate the DeepSeek-harness (DSH) widget set at the bottom of the chat, under the message input.

| # | Change | Subject |
|---|---|---|
| A | **Add** a session-statistics button + popover | new component |
| B | **Add** a token-usage button + popover | new component |
| C | **Move** the existing percentage ring from the top bar into that same new toolbar, and **upgrade its popover** to the DSH context breakdown. Ring internals (arc, colour bands, warning icon) are **not** changed. | `ContextIndicator.tsx`, `ChatWindow.tsx` |
| D | **Remove** the per-response token chip (the `DollarOutlined` button + tooltip showing `tokens_out ?? tokens_in`) from every assistant message. | `MessageBubble.tsx` lines 599–625 |

### Target rendering (authoritative)

```
[ 2 turns · 39 steps · 243 tok/s ]  [ 1.8M tok · Cache hit 98% ]  [ (9%) ]     ← toolbar
```

Stats popover — title **Session statistics**, label left / value right:

| Row | Value |
|---|---|
| LLM time | `3m18s` |
| Tool time | `4m35s` |
| Avg time to first token (TTFT) | `1.2s` |
| Tokens per second (TPS) | `243 tok/s` |

Token popover — title **Token usage**:

| Row | Value |
|---|---|
| (header total) | `1,798,557 tok` |
| Cache hit | `98%` |
| Uncached input | `42,837 tok` |
| Cached input | `1,718,912 tok` |
| Output | `36,808 tok` |

Context-ring popover (replaces today's body; the existing `Auto-compact at 75% usage` note and `Compact Conversation` button are **kept**):

```
              9%
        of context used
        ~85.4K / 1M
   ─────────────────────────
   System prompt        ~2.3K
   Tool definitions     ~7.3K
   Messages            ~72.9K
   ─────────────────────────
   Auto-compact at 75% usage
   [ Compact Conversation ]
```

### Verified metric relationships (locked into the contract)

Using the DSH sample numbers: `tokens_in = 42,837 + 1,718,912 = 1,761,749`; `tokens_out = 36,808`; `tokens_total = 1,798,557` (= `1.8M` compact); `cache_hit_percent = 1,718,912 / 1,761,749 × 100 = 97.6 → 98%`. **Cache-hit % is relative to input tokens, not total.** The breakdown rows (`2.3K + 7.3K + 72.9K = 82.5K`) intentionally **do not** sum to the `85.4K` total: the total is provider-reported `tokens_in`, the rows are local estimates. Do not "fix" this.

### Success criteria

1. Toolbar renders below the message input in non-pending sessions with the three elements above, in that order.
2. Each label and every popover string matches the tables above character-for-character.
3. The percentage ring is absent from the top bar (desktop + mobile), appears in the toolbar, and its arc/colour/warning behaviour is unchanged.
4. No assistant message renders a dollar icon or a bare per-response token number.
5. Values survive a page reload and cover turns from before it (DB-backed).
6. Legacy sessions (no recorded metrics) show `—` in timing/breakdown positions, never `0` or a wrong number.
7. `npm run build` (tsc) passes, `npm test` passes, targeted backend pytest passes.
8. `CHANGELOG.md` has an `[#531]` entry under `[Unreleased]`.

---

## 2. Why this shape

- Per-message persistence today: `messages.tokens_in`, `messages.tokens_out` only. **No cache-hit and no timing data anywhere.**
- `usage_logs` holds `cache_hit_tokens` but has **no `session_id`** → not aggregatable per session.
- `messages.content` is JSON and already carries typed parts (`reasoning`, `function_call`, `function_result`, `text`); `MessageBubble` filters by known types, so an **unknown part type is safely ignored**. → **No DB migration and no schema change are needed.**
- `_run_agent_stream(model_client, system_prompt, tools, …)` and `_run_agent(…, system_prompt, tools, …)` already receive exactly the two inputs the context breakdown needs, and `_estimate_tokens()` already exists (4 chars/token). MAF `FunctionTool` exposes `to_json_schema_spec()` → `{name, description, parameters}`, so tool-definition tokens are estimable.
- The per-response chip is one JSX block + two memo lines in a single file → a free parallel task.
- `formatTokenCount` is used **only** by `ContextIndicator` (4 assertions in its test), and its lowercase `"4.4k"` style does **not** match DSH's `"1.8M"`. → keep it untouched; add DSH-style helpers alongside it. Zero churn.

---

## 3. Frozen contract (single source of truth — do not deviate)

### 3.1 `GET /api/chat/session/{session_id}/usage` (new)

Auth/ownership must mirror `get_session_context` (`_load_session` + `_require_session_owner`).

```python
class SessionUsageResponse(BaseModel):
    turns: int = 0                    # count of sender == "user"
    steps: int = 0                    # sum of metrics.steps over assistant messages
    tokens_in: int = 0                # sum of messages.tokens_in (None -> 0)
    tokens_out: int = 0               # sum of messages.tokens_out (None -> 0)
    tokens_total: int = 0             # tokens_in + tokens_out
    cached_input_tokens: int = 0      # sum of metrics.cache_hit_tokens
    uncached_input_tokens: int = 0    # max(0, tokens_in - cached_input_tokens)
    cache_hit_percent: float | None = None  # round(cached/tokens_in*100, 1) if metrics seen AND tokens_in>0 else None
    llm_time_ms: int = 0              # sum of metrics.llm_ms
    tool_time_ms: int = 0             # sum of metrics.tool_ms
    avg_ttft_ms: int | None = None    # mean of metrics.ttft_ms over messages that have it; None if none
    tps: float | None = None          # round(tokens_out / (llm_time_ms/1000), 1) if llm_time_ms>0 else None
    has_timing_data: bool = False     # True iff any metrics part carried a non-null timing value
```

Aggregates all **non-deleted** messages from `runner._get_messages_for_session(db, session_id, is_temporary)` (covers MariaDB ORM rows and Redis temp dicts). Summarized messages are **included** (they consumed tokens historically).

### 3.2 `GET /api/chat/session/{session_id}/context` (extended — additive only)

```python
class SessionContextResponse(BaseModel):
    tokens_used: int = 0                  # UNCHANGED logic
    context_length: int | None = None     # UNCHANGED logic
    percentage: float | None = None       # UNCHANGED logic
    # NEW:
    system_prompt_tokens: int | None = None
    tool_definition_tokens: int | None = None
    messages_tokens: int | None = None
```

The three new fields are read from the **most recent assistant message's `metrics` part**; they are `None` for legacy sessions. **Do not alter** `tokens_used`, `context_length`, or `percentage` — the ring's arc, colour bands, and `≥75%` warning depend on them.

### 3.3 Persisted `metrics` part — appended as the **last** element of assistant `content`

```json
{"type":"metrics",
 "llm_ms":198000,"tool_ms":275000,"ttft_ms":1200,"steps":39,"cache_hit_tokens":1718912,
 "system_prompt_tokens":2300,"tool_definition_tokens":7300,"messages_tokens":72900}
```
- `ttft_ms` may be `null` (non-streaming path). The three breakdown fields may be `null` when `tokens_in == 0`.
- The whole part is omitted when nothing is available. No other part type changes.

### 3.4 Frontend types — `services/chat.ts`

```ts
export interface SessionUsageData { turns: number; steps: number; tokens_in: number;
  tokens_out: number; tokens_total: number; cached_input_tokens: number;
  uncached_input_tokens: number; cache_hit_percent: number | null;
  llm_time_ms: number; tool_time_ms: number; avg_ttft_ms: number | null;
  tps: number | null; has_timing_data: boolean; }
export function getSessionUsage(sessionId: string): Promise<SessionUsageData>;
// `/chat/session/${sessionId}/usage`

export interface SessionContextData {           // extend existing interface
  tokens_used: number; context_length: number | null; percentage: number | null;
  system_prompt_tokens: number | null;
  tool_definition_tokens: number | null;
  messages_tokens: number | null; }
```
React Query key: **`["sessionUsage", sessionId]`**.

### 3.5 New file `frontend/src/features/chat/utils/formatMetrics.ts`

Leave the existing `formatTokenCount` in `ContextIndicator.tsx` **exactly as is** (its lower-case style and its 4 test assertions stay green).

| Helper | Rule | Test vectors (must be asserted) |
|---|---|---|
| `formatTokensCompact(n)` | `≥1e6` → 1 decimal + `M`; `≥1e3` → 1 decimal + `K`; else digits. Trailing `.0` dropped. | `1798557→"1.8M"`, `1000000→"1M"`, `85400→"85.4K"`, `7300→"7.3K"`, `2300→"2.3K"`, `950→"950"` |
| `formatTokensExact(n)` | `n.toLocaleString("en-US")` | `1798557→"1,798,557"`, `42837→"42,837"` |
| `formatTokensApprox(n)` | `"~" + formatTokensCompact(n)`; `null→"—"` | `85400→"~85.4K"`, `null→"—"` |
| `formatDuration(ms)` | `<60s` → `(ms/1000).toFixed(1)+"s"`; `≥60s` → `"{m}m{ss}s"` with `ss` zero-padded to 2; `null→"—"` | `198000→"3m18s"`, `275000→"4m35s"`, `1200→"1.2s"`, `450→"0.5s"`, `65000→"1m05s"` |
| `formatTps(n)` | `Math.round(n)+" tok/s"`; `null→"—"` | `243.4→"243 tok/s"`, `null→"—"` |
| `formatPercent(n)` | `Math.round(n)+"%"`; `null→"—"` | `97.6→"98%"`, `9.0→"9%"`, `null→"—"` |

Use the literal em dash `—` (U+2014).

### 3.6 Component props, labels, test IDs

```ts
// Pure presentational — NO data fetching, NO query hooks
SessionStatsButtonProps { usage?: SessionUsageData; isLoading: boolean; isError: boolean }
TokenUsageButtonProps   { usage?: SessionUsageData; isLoading: boolean; isError: boolean }
SessionUsageToolbarProps{ sessionId?: string; streaming?: boolean }
```

**Labels (exact, ` · ` = space-middot-space):**
- Stats: `` `${turns} turns · ${steps} steps · ${formatTps(tps)}` `` → `2 turns · 39 steps · 243 tok/s`
- Tokens: `` `${formatTokensCompact(tokens_total)} tok · Cache hit ${formatPercent(cache_hit_percent)}` `` → `1.8M tok · Cache hit 98%`

**Test IDs (stable):**

| Element | `data-testid` |
|---|---|
| toolbar container | `session-usage-toolbar` |
| stats button / popover / loading / error | `session-stats-button`, `session-stats-popover`, `session-stats-loading`, `session-stats-error` |
| token button / popover / loading / error | `token-usage-button`, `token-usage-popover`, `token-usage-loading`, `token-usage-error` |
| context breakdown block / rows | `context-breakdown`, `context-breakdown-system`, `context-breakdown-tools`, `context-breakdown-messages` |

- `SessionUsageToolbar` owns the **single** `useQuery(["sessionUsage", sessionId])` (`enabled: !!sessionId`, `refetchInterval: streaming ? 3000 : false`) and passes `data`/`isLoading`/`isError` down. Returns `null` when `!sessionId`.
- Both popovers: `trigger="click"`, `placement="topLeft"`, title row as in §1, label left / value right, font sizes 11–13 to match `ContextIndicator`.
- Loading → spinner + `*-loading`; error → `*-error` with `n/a`. Never render `0` where the contract says `—`.
- `ContextIndicator` keeps its existing query, its `RING_SIZE`/`Progress` internals, its `aria-label`, and its `context-indicator*` test IDs. **Only its popover body and the addition of the breakdown rows change.**

### 3.7 MessageBubble removal contract

Delete exactly these, nothing else:

- JSX block `MessageBubble.tsx` **lines 599–625** (the `{(message.tokens_in != null || message.tokens_out != null) && (<Tooltip …><Button icon={<DollarOutlined />}>…</Button></Tooltip>)}` chip).
- `Tooltip` from the antd import (line 10) and `DollarOutlined` from the icons import (line 22) — both become unused and `frontend/tsconfig.json` sets `noUnusedLocals: true`, so leaving them fails the build.
- Memo comparator lines **736–737** (`tokens_in` / `tokens_out`).

Keep: the `Message` token fields and test fixtures (still used by the new endpoint), the `Text` import (9 other usages), and the live `streamingDuration` timer at line 263 (streaming progress, out of scope).

---

## 4. Shared preamble — paste once at the top of EVERY task prompt

> Repo: `/home/phalo/AI/ph-agent-hub` (branch `issue-531`). Implement ONLY the task below. Do not refactor unrelated code, do not reformat files, do not touch files outside the listed `Files:`. Follow the frozen contract exactly — signatures, field names, label strings, and test IDs are not negotiable. When done, run the listed `Done-when` command and report: files changed, exact command, exact result. If a `Done-when` command fails, fix your change; never modify the test or the contract to make it pass.

---

## 5. Tasks

### T0 — DSH formatting helpers — TINY — Wave 1
**Files:** create `frontend/src/features/chat/utils/formatMetrics.ts` and `formatMetrics.test.ts`.
**Do:** Implement the six helpers in §3.5 with the exact rules and test vectors shown. Pure functions, no React, no imports. Test file uses `import { describe, it, expect } from "vitest";` and asserts every vector in §3.5, including the `null → "—"` cases (literal U+2014 em dash).
**Done-when:** `cd frontend && npx vitest run src/features/chat/utils/formatMetrics.test.ts` passes.

---

### T1 — Frontend service types — TINY — Wave 1
**Files:** `frontend/src/features/chat/services/chat.ts`.
**Do:** Add `SessionUsageData` + `getSessionUsage` (§3.4) immediately after the existing `getSessionContext` (~line 217, in the "Session Context Window (Issue #309)" section), reusing the already-imported `api`. Extend the existing `SessionContextData` interface with the three new nullable fields. Change nothing else.
**Done-when:** `cd frontend && npx tsc --noEmit` shows no new errors.

---

### T2 — Usage endpoint + context breakdown fields — MED — Wave 1
**Files:** `backend/src/api/chat.py` (session-context section, ~line 2984 onward only).
**Do:**
1. Add `SessionUsageResponse` (§3.1) and `GET /session/{session_id}/usage` right after `get_session_context`, reusing `_load_session`, `_require_session_owner`, `_msg_get`, and `runner._get_messages_for_session`. Iterate messages once: count `sender == "user"` for `turns`; sum `metrics` parts for `steps`, `llm_ms`, `tool_ms`, `cache_hit_tokens`; collect non-null `ttft_ms` into a list; treat `None` token columns as `0`; guard `content` not being a list and items not being dicts. Apply §3.1's `None` rules exactly (`cache_hit_percent` is `None` when no `metrics` part was seen; `tps` is `None` when `llm_time_ms == 0`).
2. Add the three nullable fields to `SessionContextResponse` and populate them from the most recent assistant message's `metrics` part — **without touching** the existing `tokens_used`/`context_length`/`percentage` computation.
**Done-when:** `cd backend && ../.venv/bin/python -c "import sys; sys.path.insert(0,'.'); import src.api.chat; print('ok')"` prints ok.

---

### T3 — Runner metrics-part plumbing — MED — Wave 1
**Files:** `backend/src/agents/runner.py` (`_persist_assistant_message` ~2718, `_persist_messages` ~2787, streaming call site ~3123).
**Do:** Add an optional `metrics: dict | None = None` parameter (after `message_id`) to `_persist_assistant_message` and `_persist_messages`. When non-empty, append `{"type": "metrics", **metrics}` as the **last** element of the assistant `content` list (§3.3) on **both** branches — MariaDB (`Message(content=content, …)`) and Redis (`append_temp_message`). At the streaming call site inside `run_agent_stream`'s `finally:` block (~line 3123), first add `metrics_payload: dict | None = None` near the top of that block, then pass `metrics=metrics_payload` to `_persist_assistant_message`. Do **not** compute timings yet — behaviour must be byte-identical when `metrics is None`. Add the comment `# Issue #531 — session usage metrics (last element).`
**Done-when:** `cd backend && ../.venv/bin/python -m pytest tests/test_agent_runner.py -q` passes with no regressions.

---

### T4 — SessionStatsButton — SMALL — Wave 2 (needs T0, T1)
**Files:** create `frontend/src/features/chat/components/SessionStatsButton.tsx` and `.test.tsx`.
**Do:** Pure presentational component per §3.6. Loading → spinner + `session-stats-loading`; error → `session-stats-error` (`n/a`); loaded → text button with the §3.6 label and an antd `Popover trigger="click" placement="topLeft"` titled `Session statistics` with the four §1 rows, values via `formatDuration`/`formatTps`, `"—"` when null.
**Done-when:** `cd frontend && npx vitest run src/features/chat/components/SessionStatsButton.test.tsx` passes. Cover: all fields populated (assert `3m18s`, `4m35s`, `1.2s`, `243 tok/s`), `has_timing_data: false` (all `—`), loading, error. Open the popover with `userEvent.click`.

---

### T5 — TokenUsageButton — SMALL — Wave 2 (needs T0, T1)
**Files:** create `frontend/src/features/chat/components/TokenUsageButton.tsx` and `.test.tsx`.
**Do:** Same shape as T4 with the token test IDs and §3.6 label. Popover titled `Token usage`: header `1,798,557 tok`, then rows Cache hit `98%`, Uncached input `42,837 tok`, Cached input `1,718,912 tok`, Output `36,808 tok` — via `formatTokensExact`/`formatPercent`; `cache_hit_percent: null` → `"—"`.
**Done-when:** `cd frontend && npx vitest run src/features/chat/components/TokenUsageButton.test.tsx` passes. Assert the literal strings above; also cover `cache_hit_percent: null`, loading, error.

---

### T6 — Remove per-response token chip — TINY — Wave 1
**Files:** `frontend/src/features/chat/components/MessageBubble.tsx`; touch `MessageBubble.test.tsx` only if it asserts the removed chip (it does not — its `tokens_in/out` values are fixtures in an unrelated reasoning test).
**Do:** Apply §3.7 exactly. Keep every other action-row button and its order, and keep the `streamingDuration` timer. Ensure the `React.memo` comparator's `&&` chain remains syntactically valid after removing the two lines.
**Done-when:** `cd frontend && npx vitest run src/features/chat/components/MessageBubble.test.tsx` passes; `grep -c "DollarOutlined" src/features/chat/components/MessageBubble.tsx` prints `0`; `npx tsc --noEmit` shows no new errors.

---

### T7 — Runner timing + context-breakdown capture — MED — Wave 2 (after T3, same file)
**Files:** `backend/src/agents/runner.py` (`_run_agent_stream` ~3364, `_run_agent` ~2541, new pure helpers near `_maybe_accumulate_tool_events` ~3746).
**Do:**
1. Add `_estimate_tool_definitions(tools) -> int`: sum `_estimate_tokens(json.dumps(t.to_json_schema_spec(), default=str))` over tools, with a per-tool `try/except` falling back to `_estimate_tokens(f"{getattr(t,'name','')} {getattr(t,'description','')}")` (MCP/A2A tools may lack the method).
2. Add a pure helper `_compute_turn_metrics(*, turn_start_s, turn_end_s, first_token_at_s, tool_durations_s, steps, cache_hit_tokens, system_prompt_tokens, tool_definition_tokens, tokens_in) -> dict` returning §3.3's keys, where `llm_ms = max(0, turn_wall_ms - tool_ms)`, `ttft_ms = None` when there was no first token, `messages_tokens = max(0, tokens_in - system_prompt_tokens - tool_definition_tokens)`, and the three breakdown fields are `None` when `tokens_in == 0`.
3. In `_run_agent_stream` (which already receives `system_prompt` and `tools`): record `turn_start` before the loop; set `first_token_at` on the first emitted `token` event (**not** `reasoning_token`); record the monotonic time when a `function_call`/`tool_call` is handled, keyed by `call_id`, and add `now - start` to `tool_durations` when the matching `function_result`/`tool_result` arrives (skip unknown ids); at loop exit build the dict with `steps=step_index` and `cache_hit_tokens=_stream_token_info.get("cache", 0)`; assign it to `metrics_payload` so the `finally` block persists it.
4. In `_run_agent`, compute the same dict over the whole call with `ttft_ms=None` and `tool_ms=0`, and pass it through to `_persist_messages`.
`tool_ms` is the **sum of per-call durations** (parallel batches may exceed wall clock — intentional). Add `import time` if absent.
**Done-when:** `cd backend && ../.venv/bin/python -m pytest tests/test_agent_runner.py -q` passes; add unit tests for `_estimate_tool_definitions` (stub with `to_json_schema_spec`, plus a stub lacking it) and `_compute_turn_metrics` (normal; no first token; tool time > wall clock → `llm_ms == 0`; `tokens_in == 0` → three breakdown fields `None`).

---

### T8 — Backend endpoint tests — SMALL — Wave 2 (after T2)
**Files:** `backend/tests/test_chat_api.py` (append beside the existing context tests ~line 3340).
**Do:** For `/usage`: (a) empty session → all zeros, `cache_hit_percent is None`, `tps is None`, `has_timing_data is False`; (b) session with a user message plus an assistant message carrying `tokens_in`/`tokens_out` and a `metrics` part → assert the exact §3.1 arithmetic including `uncached_input_tokens`, `cache_hit_percent`, `avg_ttft_ms`, `tps`, `tokens_total`; (c) another user's session → 403; (d) legacy message without a `metrics` part → token totals summed, timing `0`/`None`, `has_timing_data is False`, `cache_hit_percent is None` (**not** `0.0`). For `/context`: (e) the three new fields populated from a `metrics` part, and (f) `None` for a legacy session — while `tokens_used`/`percentage` keep their current values. Reuse the file's existing fixtures.
**Done-when:** `cd backend && ../.venv/bin/python -m pytest tests/test_chat_api.py -q -k "usage or context"` passes.

---

### T9 — SessionUsageToolbar — SMALL — Wave 3 (needs T4, T5)
**Files:** create `frontend/src/features/chat/components/SessionUsageToolbar.tsx` and `.test.tsx`.
**Do:** Per §3.6 — own the single `useQuery(["sessionUsage", sessionId])`, render a flex row (`gap: 4`, `flexWrap: "wrap"`, `alignItems: "center"`) containing `SessionStatsButton`, `TokenUsageButton`, then the existing `ContextIndicator` **last**; return `null` when `!sessionId`; container gets `session-usage-toolbar`.
**Done-when:** `cd frontend && npx vitest run src/features/chat/components/SessionUsageToolbar.test.tsx` passes. Cover: no `sessionId` → nothing rendered; loaded → all three present (assert the ring via `context-indicator`); rejected service → both buttons in error state without crashing.

---

### T10 — ContextIndicator popover redesign — SMALL — Wave 2 (needs T1)
**Files:** `frontend/src/features/chat/components/ContextIndicator.tsx`, `ContextIndicator.test.tsx`.
**Do:** Replace only the **popover body** with the §1 layout: big `Math.round(percentage)` + `%`, the line `of context used`, then `` `${formatTokensApprox(tokens_used)} / ${formatTokensCompact(contextLength)}` ``, a divider, then the `context-breakdown` block with rows System prompt / Tool definitions / Messages rendered only when the corresponding new field is non-null (omit the whole block for legacy sessions). Keep the `Auto-compact at 75% usage` note and the `Compact Conversation` button, and keep every existing test ID, the `aria-label`, the ring `Progress`, `RING_SIZE`, `bandForPercentage`, `arcColorForBand`, and the local `formatTokenCount` **unchanged**. Import `formatTokensApprox`/`formatTokensCompact` from `../utils/formatMetrics` for the new lines only.
**Done-when:** `cd frontend && npx vitest run src/features/chat/components/ContextIndicator.test.tsx` passes. Update the popover assertions to the new strings; add cases for breakdown rows shown (`~2.3K`, `~7.3K`, `~72.9K`) and absent when `null`.

---

### T11 — CHANGELOG & docs — TINY — Wave 3
**Files:** `CHANGELOG.md`, `docs/user-guide.md` (and `docs/frontend-architecture.md` only if it enumerates chat components — check first).
**Do:** Under `## [Unreleased]` add a `### Changed` bullet with `[#531]`: a session-level usage toolbar below the message input with session statistics (turns, steps, tok/s; popover: LLM time, tool time, avg TTFT, TPS) and token usage (total, cache hit %, uncached input, cached input, output); the context ring moved from the top bar into that toolbar with a new context breakdown (system prompt, tool definitions, messages); and removal of the per-response token chip from assistant messages because it presented a truncated per-message count as a precise measure. Add a short user-guide paragraph and the `[#531]:` link reference if the file keeps them.
**Done-when:** `grep -n 531 CHANGELOG.md` shows the new entry; link anchors resolve.

---

### T12 — Wire into ChatWindow — SMALL — Wave 4 (needs T9)
**Files:** `frontend/src/features/chat/components/index.ts`, `ChatWindow.tsx`, `ChatWindow.test.tsx`.
**Do:** (1) Add `SessionStatsButton`, `TokenUsageButton`, `SessionUsageToolbar` to the barrel. (2) In `ChatWindow.tsx`: delete both `<ContextIndicator sessionId={sessionId} />` usages from the top bars (~line 1889 mobile, ~line 1916 desktop) and drop `ContextIndicator` from the barrel import (it now renders inside `SessionUsageToolbar`); add `<SessionUsageToolbar sessionId={sessionId} streaming={streaming} />` inside the input-area container directly after the textarea row's closing `</div>` (~line 2604), rendered only when `!pendingFlag`. (3) Add `queryClient.invalidateQueries({ queryKey: ["sessionUsage", sessionId] })` immediately after **every** existing `["sessionContext", sessionId]` invalidation (grep the file; ~10 sites). (4) In `ChatWindow.test.tsx`, add the three new components to the `vi.mock("./", …)` factory — without this the mock barrel yields `undefined` and the render crashes.
**Done-when:** `cd frontend && npx vitest run src/features/chat/components/ChatWindow.test.tsx` passes; `grep -c "ContextIndicator" src/features/chat/components/ChatWindow.tsx` prints `0`; `grep -c "sessionUsage" src/features/chat/components/ChatWindow.tsx` is ≥ 10.

---

### T13 — Frontend verification — TINY — Wave 5
**Do:** `cd frontend && npx tsc --noEmit`, then `npx vitest run`. Report full output. Do not edit source; report failures verbatim instead.
**Done-when:** both commands exit 0.

---

### T14 — Backend verification — TINY — Wave 5
**Do:** `cd backend && ../.venv/bin/python -m pytest tests/test_chat_api.py tests/test_agent_runner.py -q`. Report the outcome. Do not edit source.
**Done-when:** command exits 0 with no new failures.

---

## 6. Edge cases & failure modes

| Case | Behaviour |
|---|---|
| New/pending session (no `sessionId`) | Toolbar not rendered |
| Empty session | All zeros; `cache_hit_percent`, `avg_ttft_ms`, `tps` = `null` → `—` |
| Legacy session (pre-instrumentation) | Turns/token totals correct; timing `—`; breakdown block hidden; `cache_hit_percent: null` (**not** `0%`) |
| Genuine 0% cache hit | Renders `0%`, because a `metrics` part exists |
| Temporary (Redis) session | Works — `_get_messages_for_session` covers both stores; the part lives in the same `content` list |
| Aborted/partial stream | Metrics still persisted through the existing `finally` path |
| `content` not a list / missing | Skipped without raising; no 500 |
| `tokens_in == 0` | Breakdown fields `None` → rows omitted; `cache_hit_percent` `None` |
| Parallel tool batch | Tool time = sum of per-call durations (may exceed wall clock); `llm_ms` clamped ≥ 0 |
| Non-streaming path | `ttft_ms = null`, `tool_ms = 0`; excluded from `avg_ttft_ms` |
| MCP/A2A tool without `to_json_schema_spec` | Per-tool `try/except` falls back to name + description |
| Breakdown rows ≠ displayed total | **Expected and preserved** (estimates vs provider-reported `tokens_in`); do not reconcile |
| Polling during stream | `refetchInterval` 3 s only while `streaming`; a single React Query dedupes the toolbar |
| Unknown `metrics` part in UI | `MessageBubble` filters by known types → ignored |

---

## 7. Public API / schema / data-flow changes

- **New endpoint:** `GET /api/chat/session/{session_id}/usage`.
- **Extended endpoint:** `GET /api/chat/session/{session_id}/context` gains three nullable fields (additive).
- **New persisted JSON part type** `metrics` in `messages.content` (additive; session export/import copies `content` verbatim, so it round-trips).
- **No DB migration, no column changes.** `usage_logs` untouched; `session_id` deliberately **not** added.
- **New frontend modules:** `utils/formatMetrics.ts`, `components/SessionUsageToolbar.tsx`, `components/SessionStatsButton.tsx`, `components/TokenUsageButton.tsx`, `services/chat.ts::getSessionUsage`.
- **Removed UI:** the per-response token chip. `ContextIndicator` is **retained, relocated, and its popover enriched** — never removed.

---

## 8. Assumptions

1. `turns` = user messages in the session; `steps` = the runner's `step_index` summed per turn (parallel batches count as one step, matching `AGENT_MAX_STEPS`).
2. `cache_hit_percent` is relative to **input** tokens (`cached / tokens_in`), verified against the DSH sample numbers.
3. `tps = tokens_out / (llm_ms / 1000)`, session-wide. DSH's own figure uses a slightly different basis, so treat absolute parity as approximate; the field, label, and units match.
4. TTFT is measured from the first emitted `token` event (not `reasoning_token`); `avg_ttft_ms` is the mean over turns that recorded it.
5. `llm_ms` = turn wall clock − tool time; for the non-streaming path it equals total wall clock with `tool_ms = 0`.
6. Context breakdown uses `_estimate_tokens` (4 chars/token) for the system prompt and tool definitions, and provider-reported `tokens_in` minus those for messages — hence rows need not sum to the total.
7. Historical sessions are **not** backfilled; they degrade to `—`/hidden rather than showing wrong numbers.
8. The per-response token chip is removed with no message-level replacement; the toolbar is the only token surface (this is the issue's intent).
9. `streamingDuration` inside a streaming message is retained (distinct from session-level LLM time).
10. The toolbar renders in normal, mobile, and temporary chats; `embedded`/`widget`/`demo` keep their current top bar.

---

## 9. Out of scope

- Backfilling timing/cache/breakdown metrics for existing sessions.
- Adding `session_id` to `usage_logs`, or any new table/migration.
- Re-adding per-message token or cost display.
- Adding `cache_hit_tokens` to the `message_complete` SSE payload (the post-turn refetch covers it).
- Changing the ring's arc colours, severity bands, `≥75%` warning, `RING_SIZE`, or `aria-label`.
- Any cost/currency display.

---

## 10. Manual acceptance checklist (after T13/T14 pass)

1. Populated session → toolbar shows `N turns · M steps · X tok/s`, `Y tok · Cache hit Z%`, and the ring, in that order, directly under the input.
2. Clicking the stats button shows **Session statistics** with LLM time / Tool time / Avg time to first token (TTFT) / Tokens per second (TPS) formatted like `3m18s`, `4m35s`, `1.2s`, `243 tok/s`.
3. Clicking the token button shows **Token usage** with the exact-token line and the four labelled rows, comma-separated like `1,798,557 tok` / `42,837 tok`.
4. Clicking the ring shows big `9%`, `of context used`, `~85.4K / 1M`, and the three breakdown rows; the `Compact Conversation` button still works and the arc colour/warning icon still respond to the 60%/75% bands.
5. Every assistant message action row has only copy / feedback / edit / regenerate / delete — no dollar icon, no bare token number.
6. Send a message → counters increase without a manual reload; while streaming they refresh about every 3 s.
7. Reload → same values (proves DB-backed).
8. Legacy session → token totals and turns non-zero; timing rows `—`; breakdown block hidden.
9. Mobile width → toolbar wraps without horizontal overflow; no ring in the mobile top bar.
10. Brand-new pending chat → no toolbar until the first message is sent.
