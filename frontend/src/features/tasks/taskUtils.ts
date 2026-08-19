import type { Task } from "../../types";

const TERMINAL_TASK_STATUSES = new Set(["succeeded", "failed", "cancelled", "stale"]);

export function isTaskTerminal(status: string): boolean {
  return TERMINAL_TASK_STATUSES.has(status);
}

export function taskEvents(task: Task): Record<string, unknown>[] {
  const events = task.metadata?.events;
  return Array.isArray(events) ? events.filter(isRecord) : [];
}

export function taskProgress(progress: number): number {
  return Math.max(0, Math.min(progress, 100));
}

export function taskResultAnswer(task: Task): string | null {
  const answer = task.result?.answer;
  return typeof answer === "string" && answer.trim() ? answer : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
