import { Loader2, Pause, Play, RotateCw } from "lucide-react";

import type { TaskSchedule } from "../../types";

type ScheduleListProps = {
  schedules: TaskSchedule[];
  error: string;
  isLoading: boolean;
  updatingIds: Set<string>;
  onToggle: (schedule: TaskSchedule) => void;
  onRunNow: (schedule: TaskSchedule) => void;
};

export function scheduleDescription(schedule: TaskSchedule): string {
  if (schedule.schedule_kind === "interval") {
    const minutes = Math.max(Math.round((schedule.interval_seconds ?? 60) / 60), 1);
    return `Every ${minutes} minute${minutes === 1 ? "" : "s"}`;
  }
  if (schedule.schedule_kind === "daily") {
    return `Daily at ${schedule.daily_time ?? "--:--"} · ${schedule.timezone}`;
  }
  return "One-time run";
}

export function formatScheduleTime(value: string | null): string {
  if (!value) return "No next run";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}

export function ScheduleList({
  schedules,
  error,
  isLoading,
  updatingIds,
  onToggle,
  onRunNow
}: ScheduleListProps) {
  return (
    <div className="stack-list" aria-live="polite" aria-busy={isLoading}>
      {error ? <p className="inline-error">{error}</p> : null}
      {isLoading ? (
        <p className="muted task-loading">
          <Loader2 className="spin" size={15} aria-hidden="true" />
          Loading schedules...
        </p>
      ) : null}
      {!isLoading && schedules.length === 0 ? (
        <p className="muted">No recurring or scheduled runs for this conversation.</p>
      ) : (
        schedules.map((schedule) => {
          const isUpdating = updatingIds.has(schedule.id);
          return (
            <article className="schedule-card" key={schedule.id}>
              <div className="task-card-header">
                <strong title={schedule.name}>{schedule.name}</strong>
                <span className={`status-pill ${schedule.enabled ? "running" : "cancelled"}`}>
                  {schedule.enabled ? "active" : "paused"}
                </span>
              </div>
              <p>{scheduleDescription(schedule)}</p>
              <span className="schedule-next">
                Next: {formatScheduleTime(schedule.next_run_at)}
              </span>
              <div className="schedule-actions">
                <button
                  className="background-task-button compact"
                  type="button"
                  disabled={isUpdating}
                  onClick={() => onRunNow(schedule)}
                >
                  {isUpdating ? (
                    <Loader2 className="spin" size={15} aria-hidden="true" />
                  ) : (
                    <RotateCw size={15} aria-hidden="true" />
                  )}
                  Run now
                </button>
                <button
                  className="background-task-button compact"
                  type="button"
                  disabled={isUpdating}
                  aria-pressed={!schedule.enabled}
                  onClick={() => onToggle(schedule)}
                >
                  {schedule.enabled ? (
                    <Pause size={15} aria-hidden="true" />
                  ) : (
                    <Play size={15} aria-hidden="true" />
                  )}
                  {schedule.enabled ? "Pause" : "Resume"}
                </button>
              </div>
            </article>
          );
        })
      )}
    </div>
  );
}
