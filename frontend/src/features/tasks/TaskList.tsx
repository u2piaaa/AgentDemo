import { Loader2, X } from "lucide-react";

import type { Task } from "../../types";
import { isTaskTerminal, taskEvents, taskProgress, taskResultAnswer } from "./taskUtils";

type TaskListProps = {
  tasks: Task[];
  error: string;
  isLoading: boolean;
  cancellingTaskIds: Set<string>;
  onCancel: (taskId: string) => void;
};

export function TaskList({
  tasks,
  error,
  isLoading,
  cancellingTaskIds,
  onCancel
}: TaskListProps) {
  return (
    <div className="stack-list" aria-live="polite" aria-busy={isLoading}>
      {error ? <p className="inline-error">{error}</p> : null}
      {isLoading ? (
        <p className="muted task-loading">
          <Loader2 className="spin" size={15} aria-hidden="true" />
          Loading current session tasks...
        </p>
      ) : null}
      {!isLoading && tasks.length === 0 ? (
        <p className="muted">No tasks for this conversation.</p>
      ) : (
        tasks.map((task) => (
          <TaskCard
            key={task.id}
            task={task}
            isCancelling={cancellingTaskIds.has(task.id)}
            onCancel={onCancel}
          />
        ))
      )}
    </div>
  );
}

type TaskCardProps = {
  task: Task;
  isCancelling: boolean;
  onCancel: (taskId: string) => void;
};

function TaskCard({ task, isCancelling, onCancel }: TaskCardProps) {
  const canCancel = !isTaskTerminal(task.status);
  const events = taskEvents(task);
  const progress = taskProgress(task.progress);
  const answer = taskResultAnswer(task);

  return (
    <article className="task-card">
      <div className="task-card-header">
        <strong title={task.name}>{task.name}</strong>
        <span className={`status-pill ${task.status}`}>{task.status}</span>
      </div>
      <div
        className="task-progress"
        role="progressbar"
        aria-label={`${task.name} progress`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={progress}
      >
        <span style={{ width: `${progress}%` }} />
      </div>
      <div className="task-meta">
        <span>{progress}%</span>
        {task.kind === "agent" ? <span>background agent</span> : null}
        {task.trace_id ? <span title={task.trace_id}>{task.trace_id.slice(0, 10)}</span> : null}
      </div>
      {events.length ? (
        <div className="task-events" aria-label="Latest task events">
          {events.slice(-3).map((event, index) => (
            <span key={`${task.id}-event-${index}`}>
              {String(event.type ?? "event")}
              {event.label ? ` · ${String(event.label)}` : ""}
              {event.server_name ? ` · ${String(event.server_name)}` : ""}
            </span>
          ))}
        </div>
      ) : null}
      {answer ? <p className="task-result">{answer}</p> : null}
      {task.error ? <p className="inline-error">{task.error}</p> : null}
      <button
        className="danger-action compact"
        type="button"
        disabled={!canCancel || isCancelling}
        onClick={() => onCancel(task.id)}
      >
        {isCancelling ? (
          <Loader2 className="spin" size={15} aria-hidden="true" />
        ) : (
          <X size={15} aria-hidden="true" />
        )}
        {isCancelling ? "Cancelling" : "Cancel"}
      </button>
    </article>
  );
}
