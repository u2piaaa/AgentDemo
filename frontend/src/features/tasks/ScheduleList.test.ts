import { describe, expect, it } from "vitest";

import type { TaskSchedule } from "../../types";
import { scheduleDescription } from "./ScheduleList";

function makeSchedule(overrides: Partial<TaskSchedule> = {}): TaskSchedule {
  return {
    id: "schedule-1",
    conversation_id: "conversation-1",
    name: "Research briefing",
    prompt: "Summarize updates",
    schedule_kind: "interval",
    timezone: "UTC",
    run_at: null,
    interval_seconds: 3600,
    daily_time: null,
    max_attempts: 3,
    next_run_at: "2026-09-04T04:00:00Z",
    last_run_at: null,
    last_task_id: null,
    enabled: true,
    created_at: "2026-09-04T03:00:00Z",
    updated_at: "2026-09-04T03:00:00Z",
    ...overrides
  };
}

describe("schedule helpers", () => {
  it("describes interval schedules in minutes", () => {
    expect(scheduleDescription(makeSchedule())).toBe("Every 60 minutes");
  });

  it("includes timezone for daily schedules", () => {
    expect(
      scheduleDescription(
        makeSchedule({
          schedule_kind: "daily",
          daily_time: "09:30",
          timezone: "Asia/Hong_Kong"
        })
      )
    ).toBe("Daily at 09:30 · Asia/Hong_Kong");
  });
});
