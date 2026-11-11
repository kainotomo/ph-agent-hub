// =============================================================================
// PH Agent Hub — Client run state machine (Issue #609)
// =============================================================================
// One explicit state machine per visible conversation, so a run can never
// end silently.  The transport closing is not evidence of success: a stream
// that closes while `running` becomes `lost`, and the terminal state is
// rendered from the authoritative server record when available.
//
//   idle → submitting → running → settling → done
//                          └──────────────→ failed | cancelled | lost
//
// The reducer is pure so every terminal path is unit-testable.
// =============================================================================

export type RunPhase =
  | "idle"
  | "submitting"
  | "running"
  | "settling"
  | "done"
  | "failed"
  | "cancelled"
  | "lost";

export type RunTerminalOutcome =
  | "completed"
  | "failed"
  | "cancelled"
  | "lost"
  | string;

export interface RunSnapshot {
  phase: RunPhase;
  runId: string | null;
  messageId: string | null;
  errorCode: string | null;
  reason: string | null;
  persisted: boolean;
  /** True once the producer's `message_complete` arrived for this turn. */
  sawMessageComplete: boolean;
  /** The run belongs to another tab/device (HTTP 409). */
  otherClient: boolean;
}

export type RunInput =
  | { type: "submit" }
  | { type: "stream_open"; runId?: string | null }
  | { type: "message_complete"; messageId?: string | null }
  | {
      type: "run_terminal";
      outcome: RunTerminalOutcome;
      errorCode?: string | null;
      message?: string | null;
      messageId?: string | null;
      persisted?: boolean;
    }
  | { type: "error"; code?: string | null; message?: string | null }
  | { type: "cancel_requested" }
  | { type: "stream_closed" }
  | { type: "stall" }
  | { type: "busy"; runId?: string | null }
  | {
      type: "server_state";
      state: string;
      runId?: string | null;
      errorCode?: string | null;
      message?: string | null;
      messageId?: string | null;
      persisted?: boolean;
    }
  | { type: "dismiss" }
  | { type: "reset" };

export const INITIAL_RUN_STATE: RunSnapshot = {
  phase: "idle",
  runId: null,
  messageId: null,
  errorCode: null,
  reason: null,
  persisted: false,
  sawMessageComplete: false,
  otherClient: false,
};

/** Phases from which no further transition may occur without a new submit. */
export const TERMINAL_PHASES: readonly RunPhase[] = [
  "done",
  "failed",
  "cancelled",
  "lost",
];

export function isTerminalPhase(phase: RunPhase): boolean {
  return TERMINAL_PHASES.includes(phase);
}

function failed(
  prev: RunSnapshot,
  errorCode: string,
  reason: string,
): RunSnapshot {
  return {
    ...prev,
    phase: "failed",
    errorCode,
    reason,
    persisted: false,
    otherClient: false,
  };
}

function lost(prev: RunSnapshot, reason: string): RunSnapshot {
  return {
    ...prev,
    phase: "lost",
    errorCode: prev.errorCode ?? "connection_lost",
    reason,
    otherClient: false,
  };
}

function applyOutcome(
  prev: RunSnapshot,
  input: Extract<RunInput, { type: "run_terminal" }>,
): RunSnapshot {
  const messageId = input.messageId ?? prev.messageId;
  switch (input.outcome) {
    case "completed": {
      // `completed` requires a persisted answer.  When the producer does
      // not supply `persisted`, the earlier `message_complete` is the
      // evidence that the row exists.
      const persisted =
        input.persisted === true ||
        (input.persisted === undefined && prev.sawMessageComplete);
      if (!persisted) {
        return failed(
          prev,
          input.errorCode || "not_persisted",
          input.message ||
            "The run ended without saving an answer. Please try again.",
        );
      }
      return {
        ...prev,
        phase: "done",
        messageId,
        persisted: true,
        errorCode: null,
        reason: null,
        otherClient: false,
      };
    }
    case "cancelled":
      return {
        ...prev,
        phase: "cancelled",
        messageId,
        reason: input.message || "Generation stopped.",
        errorCode: null,
        otherClient: false,
      };
    case "lost":
      return lost(
        prev,
        input.message ||
          "Lost the connection to this run. The answer may still arrive; retry to continue.",
      );
    case "failed":
    default:
      return failed(
        prev,
        input.errorCode || "agent_error",
        input.message || "The agent failed before producing an answer.",
      );
  }
}

function applyServerState(
  prev: RunSnapshot,
  input: Extract<RunInput, { type: "server_state" }>,
): RunSnapshot {
  // A different run id means the server moved on: never carry the old
  // terminal state into the new run.
  let base = prev;
  if (input.runId && prev.runId && input.runId !== prev.runId) {
    base = { ...INITIAL_RUN_STATE, runId: input.runId };
  }

  const messageId = input.messageId ?? base.messageId;

  switch (input.state) {
    case "running":
      if (isTerminalPhase(base.phase)) return base;
      return {
        ...base,
        phase: "running",
        runId: input.runId ?? base.runId,
        otherClient: false,
      };
    case "completed": {
      const persisted =
        input.persisted === true || (input.persisted === undefined && !!messageId);
      if (!persisted) {
        return failed(
          base,
          input.errorCode || "not_persisted",
          input.message ||
            "The server recorded the run as completed without saving an answer.",
        );
      }
      return {
        ...base,
        phase: "done",
        messageId,
        persisted: true,
        errorCode: null,
        reason: null,
        otherClient: false,
      };
    }
    case "failed":
      return failed(
        base,
        input.errorCode || "agent_error",
        input.message || "The run failed on the server.",
      );
    case "cancelled":
      return {
        ...base,
        phase: "cancelled",
        messageId,
        errorCode: null,
        reason: input.message || "Generation stopped.",
        otherClient: false,
      };
    case "lost":
      return lost(
        base,
        input.message ||
          "The server lost this run (it stopped before reporting a result).",
      );
    case "idle":
      // Do not clobber a locally live turn: the write that created the row
      // may not be visible yet.  Adopting "idle" over `running` would drop
      // a spinner for a run that is genuinely alive.
      return base;
    default:
      // A finished row without a known outcome is a lost run, not success.
      return lost(base, input.message || "The run ended without a recorded outcome.");
  }
}

/** Pure reducer — every transport/server input ends in a declared phase. */
export function reduceRunState(
  prev: RunSnapshot,
  input: RunInput,
): RunSnapshot {
  switch (input.type) {
    case "reset":
      return { ...INITIAL_RUN_STATE };
    case "dismiss":
      return isTerminalPhase(prev.phase) ? { ...INITIAL_RUN_STATE } : prev;
    case "submit":
      return { ...INITIAL_RUN_STATE, phase: "submitting" };
    case "stream_open": {
      if (isTerminalPhase(prev.phase)) return prev;
      return {
        ...prev,
        phase: "running",
        runId: input.runId ?? prev.runId,
        otherClient: false,
        reason: null,
      };
    }
    case "message_complete": {
      if (isTerminalPhase(prev.phase)) return prev;
      return {
        ...prev,
        phase: "settling",
        messageId: input.messageId ?? prev.messageId,
        sawMessageComplete: true,
      };
    }
    case "run_terminal":
      return applyOutcome(prev, input);
    case "error": {
      // A cancel is shown as cancelled, never as an error.
      if (prev.phase === "cancelled" || isTerminalPhase(prev.phase)) return prev;
      return failed(
        prev,
        input.code || "agent_error",
        input.message || "The request failed. Please try again.",
      );
    }
    case "cancel_requested":
      return {
        ...prev,
        phase: "cancelled",
        errorCode: null,
        reason: "Generation stopped.",
        otherClient: false,
      };
    case "stream_closed": {
      switch (prev.phase) {
        case "submitting":
          return failed(
            prev,
            "stream_closed_before_start",
            "The request ended before the run started. Please try again.",
          );
        case "running":
          return lost(
            prev,
            "The connection closed while the run was still working. " +
              "The answer may still arrive; retry to continue.",
          );
        case "settling":
          return { ...prev, phase: "done", persisted: true };
        default:
          return prev;
      }
    }
    case "stall":
      // The watchdog is reconnecting from the cursor — still running, but
      // if the reconnect gives up the close handler decides the terminal.
      return prev;
    case "busy":
      if (isTerminalPhase(prev.phase)) return prev;
      return {
        ...prev,
        phase: "running",
        runId: input.runId ?? prev.runId,
        otherClient: true,
        reason: "This conversation is already running in another window.",
      };
    case "server_state":
      return applyServerState(prev, input);
  }
}

// ---------------------------------------------------------------------------
// User-facing copy
// ---------------------------------------------------------------------------

const ERROR_TEXT: Record<string, string> = {
  auth_error: "Your session expired. Please sign in again.",
  auth: "Your session expired. Please sign in again.",
  model_timeout: "The model took too long to respond. Please try again.",
  empty_output: "The run ended without producing an answer. Please try again.",
  not_persisted: "The run ended without saving an answer. Please try again.",
  missing_terminal:
    "The run stopped before reporting a result. Please try again.",
  agent_error: "The agent failed before producing an answer. Please try again.",
  tool_error: "A tool failed before the answer was complete. Please try again.",
  connection_lost: "Lost the connection to this run.",
  orphaned:
    "The server restarted before this run finished. The answer was not saved.",
};

export interface TerminalCopy {
  title: string;
  description: string;
  retryable: boolean;
}

/** Inline copy for a non-success terminal state (`done` → null). */
export function terminalCopy(snapshot: RunSnapshot): TerminalCopy | null {
  switch (snapshot.phase) {
    case "failed":
      return {
        title: "The response failed",
        description:
          snapshot.reason ||
          (snapshot.errorCode ? ERROR_TEXT[snapshot.errorCode] : undefined) ||
          "The run failed before producing an answer. Please try again.",
        retryable: true,
      };
    case "lost":
      return {
        title: "Lost connection to the run",
        description:
          snapshot.reason ||
          ERROR_TEXT.lost ||
          "The server stopped reporting this run. The answer may not have been saved.",
        retryable: true,
      };
    case "cancelled":
      return {
        title: "Generation stopped",
        description:
          "The partial answer was kept. Retry to continue this turn.",
        retryable: true,
      };
    default:
      return null;
  }
}
