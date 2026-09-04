import { CalendarClock, Loader2, X } from "lucide-react";
import { useState } from "react";

import type { TaskScheduleCreateInput } from "../../types";

type ScheduleDraft = Omit<TaskScheduleCreateInput, "prompt" | "conversation_id">;

type ScheduleComposerProps = {
  open: boolean;
  disabled: boolean;
  isCreating: boolean;
  error: string;
  onClose: () => void;
  onCreate: (draft: ScheduleDraft) => Promise<void>;
};

function defaultRunAt(): string {
  const date = new Date(Date.now() + 60 * 60 * 1000);
  date.setSeconds(0, 0);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function browserTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

export function ScheduleComposer({
  open,
  disabled,
  isCreating,
  error,
  onClose,
  onCreate
}: ScheduleComposerProps) {
  const [kind, setKind] = useState<ScheduleDraft["schedule_kind"]>("once");
  const [runAt, setRunAt] = useState(defaultRunAt);
  const [intervalMinutes, setIntervalMinutes] = useState(60);
  const [dailyTime, setDailyTime] = useState("09:00");
  const [timezone, setTimezone] = useState(browserTimezone);
  const [maxAttempts, setMaxAttempts] = useState(3);
  const [validationError, setValidationError] = useState("");

  if (!open) return null;

  async function submitSchedule() {
    setValidationError("");
    if (!Number.isInteger(maxAttempts) || maxAttempts < 1 || maxAttempts > 10) {
      setValidationError("Maximum attempts must be between 1 and 10.");
      return;
    }
    const draft: ScheduleDraft = {
      schedule_kind: kind,
      timezone: timezone.trim() || "UTC",
      max_attempts: maxAttempts
    };
    if (kind === "once") {
      const parsed = new Date(runAt);
      if (!runAt || Number.isNaN(parsed.getTime()) || parsed.getTime() <= Date.now()) {
        setValidationError("Choose a future date and time.");
        return;
      }
      draft.run_at = parsed.toISOString();
    } else if (kind === "interval") {
      if (!Number.isInteger(intervalMinutes) || intervalMinutes < 1) {
        setValidationError("Interval must be at least 1 minute.");
        return;
      }
      draft.interval_minutes = intervalMinutes;
    } else {
      if (!/^\d{2}:\d{2}$/.test(dailyTime)) {
        setValidationError("Choose a valid daily time.");
        return;
      }
      draft.daily_time = dailyTime;
    }
    await onCreate(draft);
  }

  return (
    <fieldset
      className="schedule-composer"
      disabled={disabled || isCreating}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          void submitSchedule();
        }
      }}
    >
      <legend>
        <CalendarClock size={17} aria-hidden="true" />
        Schedule this prompt
      </legend>
      <button
        className="schedule-close"
        type="button"
        aria-label="Close schedule options"
        title="Close schedule options"
        onClick={onClose}
      >
        <X size={17} aria-hidden="true" />
      </button>

      <label htmlFor="schedule-kind">Frequency</label>
      <select
        id="schedule-kind"
        value={kind}
        onChange={(event) => setKind(event.target.value as ScheduleDraft["schedule_kind"])}
      >
        <option value="once">One time</option>
        <option value="interval">Fixed interval</option>
        <option value="daily">Daily</option>
      </select>

      {kind === "once" ? (
        <>
          <label htmlFor="schedule-run-at">Run at</label>
          <input
            id="schedule-run-at"
            type="datetime-local"
            value={runAt}
            onChange={(event) => setRunAt(event.target.value)}
          />
        </>
      ) : null}
      {kind === "interval" ? (
        <>
          <label htmlFor="schedule-interval">Every (minutes)</label>
          <input
            id="schedule-interval"
            type="number"
            min={1}
            max={525600}
            value={intervalMinutes}
            onChange={(event) => setIntervalMinutes(Number(event.target.value))}
          />
        </>
      ) : null}
      {kind === "daily" ? (
        <>
          <label htmlFor="schedule-daily-time">Local time</label>
          <input
            id="schedule-daily-time"
            type="time"
            value={dailyTime}
            onChange={(event) => setDailyTime(event.target.value)}
          />
        </>
      ) : null}

      <label htmlFor="schedule-timezone">Timezone</label>
      <input
        id="schedule-timezone"
        value={timezone}
        onChange={(event) => setTimezone(event.target.value)}
        aria-describedby="schedule-timezone-help"
      />
      <small id="schedule-timezone-help">Use an IANA timezone, such as Asia/Hong_Kong.</small>

      <label htmlFor="schedule-attempts">Maximum attempts</label>
      <input
        id="schedule-attempts"
        type="number"
        min={1}
        max={10}
        value={maxAttempts}
        onChange={(event) => setMaxAttempts(Number(event.target.value))}
      />

      {validationError ? <p className="inline-error" role="alert">{validationError}</p> : null}
      {error ? <p className="inline-error" role="alert">{error}</p> : null}
      <button
        className="primary-action schedule-submit"
        type="button"
        disabled={disabled || isCreating}
        onClick={() => void submitSchedule()}
      >
        {isCreating ? (
          <Loader2 className="spin" size={17} aria-hidden="true" />
        ) : (
          <CalendarClock size={17} aria-hidden="true" />
        )}
        {isCreating ? "Scheduling" : "Create schedule"}
      </button>
    </fieldset>
  );
}
