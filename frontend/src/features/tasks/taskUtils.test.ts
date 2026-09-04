import { describe, expect, it } from "vitest";

import type { Task } from "../../types";
import {
  isTaskTerminal,
  taskEvents,
  taskProgress,
  taskResultAnswer,
  taskStatusLabel
} from "./taskUtils";

function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "task-1",
    conversation_id: "conversation-1",
    schedule_id: null,
    name: "Background task",
    kind: "agent",
    input: { prompt: "Analyze" },
    status: "running",
    progress: 50,
    error: null,
    result: null,
    trace_id: null,
    idempotency_key: null,
    attempt_count: 1,
    max_attempts: 3,
    next_attempt_at: null,
    heartbeat_at: null,
    lease_expires_at: null,
    metadata: {},
    created_at: "2026-08-19T00:00:00Z",
    started_at: null,
    finished_at: null,
    ...overrides
  };
}

describe("task helpers", () => {
  it("recognizes terminal states", () => {
    expect(isTaskTerminal("succeeded")).toBe(true);
    expect(isTaskTerminal("cancelled")).toBe(true);
    expect(isTaskTerminal("running")).toBe(false);
  });

  it("clamps progress for accessible rendering", () => {
    expect(taskProgress(-10)).toBe(0);
    expect(taskProgress(45)).toBe(45);
    expect(taskProgress(120)).toBe(100);
  });

  it("describes background task terminal states", () => {
    expect(taskStatusLabel("succeeded")).toBe("Background task completed");
    expect(taskStatusLabel("failed")).toBe("Background task failed");
    expect(taskStatusLabel("cancelled")).toBe("Background task cancelled");
    expect(taskStatusLabel("stale")).toBe("Background task interrupted");
  });

  it("filters malformed events and exposes a non-empty answer", () => {
    const task = makeTask({
      metadata: { events: [{ type: "plan" }, null, "bad"] },
      result: { answer: "Done" }
    });

    expect(taskEvents(task)).toEqual([{ type: "plan" }]);
    expect(taskResultAnswer(task)).toBe("Done");
  });
});
