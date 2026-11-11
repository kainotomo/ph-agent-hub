// =============================================================================
// runStateMachine — Issue #609
// =============================================================================
// Every terminal path must be declared and rendered.  The invariant test at
// the bottom enumerates inputs and asserts that no sequence can leave the UI
// in `running` after the stream closed.
// =============================================================================

import { describe, it, expect } from "vitest";
import {
  INITIAL_RUN_STATE,
  isTerminalPhase,
  reduceRunState,
  terminalCopy,
  type RunInput,
  type RunSnapshot,
} from "./runStateMachine";

const reduce = (snapshot: RunSnapshot, ...inputs: RunInput[]) =>
  inputs.reduce(reduceRunState, snapshot);

describe("runStateMachine", () => {
  it("starts idle", () => {
    expect(INITIAL_RUN_STATE.phase).toBe("idle");
  });

  it("submit → running on stream open", () => {
    const state = reduce(
      INITIAL_RUN_STATE,
      { type: "submit" },
      { type: "stream_open", runId: "run-1" },
    );
    expect(state.phase).toBe("running");
    expect(state.runId).toBe("run-1");
  });

  it("message_complete moves to settling, then close is done", () => {
    const state = reduce(
      INITIAL_RUN_STATE,
      { type: "submit" },
      { type: "stream_open" },
      { type: "message_complete", messageId: "msg-1" },
      { type: "stream_closed" },
    );
    expect(state.phase).toBe("done");
    expect(state.messageId).toBe("msg-1");
  });

  it("run_terminal completed with persisted=true is done", () => {
    const state = reduce(
      INITIAL_RUN_STATE,
      { type: "submit" },
      { type: "stream_open" },
      {
        type: "run_terminal",
        outcome: "completed",
        persisted: true,
        messageId: "msg-1",
      },
    );
    expect(state.phase).toBe("done");
    expect(state.persisted).toBe(true);
  });

  it("run_terminal completed without persistence is failed, never done", () => {
    const state = reduce(
      INITIAL_RUN_STATE,
      { type: "submit" },
      { type: "stream_open" },
      {
        type: "run_terminal",
        outcome: "completed",
        persisted: false,
        errorCode: "not_persisted",
      },
    );
    expect(state.phase).toBe("failed");
    expect(state.errorCode).toBe("not_persisted");
  });

  it("run_terminal failed/cancelled/lost map to their phases", () => {
    const base = reduce(
      INITIAL_RUN_STATE,
      { type: "submit" },
      { type: "stream_open" },
    );
    expect(
      reduce(base, { type: "run_terminal", outcome: "failed", errorCode: "model_timeout" }).phase,
    ).toBe("failed");
    expect(
      reduce(base, { type: "run_terminal", outcome: "cancelled" }).phase,
    ).toBe("cancelled");
    expect(reduce(base, { type: "run_terminal", outcome: "lost" }).phase).toBe(
      "lost",
    );
  });

  it("a stream that closes while running becomes lost, not idle", () => {
    const state = reduce(
      INITIAL_RUN_STATE,
      { type: "submit" },
      { type: "stream_open" },
      { type: "stream_closed" },
    );
    expect(state.phase).toBe("lost");
    expect(isTerminalPhase(state.phase)).toBe(true);
  });

  it("a stream that closes before opening is an explicit failure", () => {
    const state = reduce(
      INITIAL_RUN_STATE,
      { type: "submit" },
      { type: "stream_closed" },
    );
    expect(state.phase).toBe("failed");
    expect(state.errorCode).toBe("stream_closed_before_start");
  });

  it("cancellation is cancelled even if an error arrives afterwards", () => {
    const state = reduce(
      INITIAL_RUN_STATE,
      { type: "submit" },
      { type: "stream_open" },
      { type: "cancel_requested" },
      { type: "error", code: "agent_error", message: "boom" },
    );
    expect(state.phase).toBe("cancelled");
    expect(state.errorCode).toBeNull();
  });

  it("409 marks the phase running and names the other client", () => {
    const state = reduce(
      INITIAL_RUN_STATE,
      { type: "submit" },
      { type: "busy", runId: "other-run" },
    );
    expect(state.phase).toBe("running");
    expect(state.otherClient).toBe(true);
    expect(state.reason).toMatch(/another window/i);
  });

  it("server state reconciles terminal outcomes", () => {
    const live = reduce(
      INITIAL_RUN_STATE,
      { type: "submit" },
      { type: "stream_open" },
    );
    const failed = reduce(live, {
      type: "server_state",
      state: "failed",
      errorCode: "model_timeout",
    });
    expect(failed.phase).toBe("failed");
    expect(failed.errorCode).toBe("model_timeout");

    const lost = reduce(
      { ...live, runId: "run-old" },
      { type: "server_state", state: "lost", runId: "run-new" },
    );
    expect(lost.phase).toBe("lost");
  });

  it("server completed without persistence is failed", () => {
    const state = reduce(
      INITIAL_RUN_STATE,
      { type: "submit" },
      { type: "stream_open" },
      { type: "server_state", state: "completed", persisted: false },
    );
    expect(state.phase).toBe("failed");
    expect(state.errorCode).toBe("not_persisted");
  });

  it("server idle does not clobber a live local turn", () => {
    const live = reduce(
      INITIAL_RUN_STATE,
      { type: "submit" },
      { type: "stream_open" },
    );
    expect(reduce(live, { type: "server_state", state: "idle" }).phase).toBe(
      "running",
    );
  });

  it("terminal states ignore later transport events", () => {
    const done = reduce(
      INITIAL_RUN_STATE,
      { type: "submit" },
      { type: "stream_open" },
      { type: "message_complete" },
      { type: "stream_closed" },
    );
    expect(reduce(done, { type: "stream_open" }).phase).toBe("done");
    expect(reduce(done, { type: "error", message: "late" }).phase).toBe("done");
    expect(reduce(done, { type: "busy" }).phase).toBe("done");
  });

  it("dismiss clears a terminal banner", () => {
    const failed = reduce(
      INITIAL_RUN_STATE,
      { type: "submit" },
      { type: "error", message: "nope" },
    );
    expect(reduce(failed, { type: "dismiss" }).phase).toBe("idle");
  });

  it("copy exists for every non-success terminal phase", () => {
    for (const input of [
      { type: "error", message: "nope" },
      { type: "run_terminal", outcome: "lost" },
      { type: "run_terminal", outcome: "cancelled" },
    ] as RunInput[]) {
      const state = reduce(
        INITIAL_RUN_STATE,
        { type: "submit" },
        { type: "stream_open" },
        input,
      );
      const copy = terminalCopy(state);
      expect(copy).not.toBeNull();
      expect(copy!.retryable).toBe(true);
      expect(copy!.description.length).toBeGreaterThan(0);
    }
    expect(
      terminalCopy(
        reduce(INITIAL_RUN_STATE, { type: "submit" }, { type: "stream_open" }),
      ),
    ).toBeNull();
  });

  // ---- Invariant: no path leaves the UI running after the stream closes ----
  it("never remains running after stream_closed (exhaustive input combos)", () => {
    const preludes: RunInput[][] = [
      [{ type: "submit" }],
      [{ type: "submit" }, { type: "stream_open" }],
      [
        { type: "submit" },
        { type: "stream_open" },
        { type: "message_complete", messageId: "m" },
      ],
      [
        { type: "submit" },
        { type: "busy", runId: "r" },
      ],
      [
        { type: "submit" },
        { type: "stream_open" },
        { type: "stall" },
      ],
    ];

    for (const prelude of preludes) {
      const state = reduce(INITIAL_RUN_STATE, ...prelude, {
        type: "stream_closed",
      });
      expect(state.phase).not.toBe("running");
      expect(state.phase).not.toBe("submitting");
      expect(state.phase).not.toBe("settling");
    }
  });
});
