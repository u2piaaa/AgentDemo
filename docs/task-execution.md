# Background execution and schedules

## Execution contract

Task records remain the durable queue. Workers claim queued agent tasks with
PostgreSQL `FOR UPDATE SKIP LOCKED` and create a new UUID execution token for
each attempt. Heartbeats, progress, completion, graph message writes, and the
`ToolExecutor` dispatch boundary lock and re-read the row before mutation.
Ownership requires matching token, running state and an unexpired lease.
Graph child tasks inherit this identity through a context variable; no public
request can provide a worker token or mutate agent task state.

The worker persists a conversation before starting the graph. User messages
carry `agent_task_id` and are deduplicated under the task lock; retry history
excludes that task's own partial turn. Saved answers retain the same identity.
The existing LangGraph nodes, SSE events, ownership checks and tool audit path
remain in use.

`timeout_seconds` limits one attempt (5–3600, default 180).
`deadline_seconds` sets an absolute deadline from API creation (5–86400,
default 900), including queueing and retries. The shorter of the task limit,
remaining deadline and existing graph budget wins. Retry delays use equal
jitter in the upper half of exponential backoff, capped at one hour, and
cannot exceed the deadline. Shutdown consumes the current attempt; repeated
restarts cannot reset the retry budget.

The process holds at most twice its configured concurrency in local jobs;
overflow and retries remain in PostgreSQL. Ready queries and recovery batches
are ordered and bounded, with a partial queue index. Concurrency is per process;
multiple processes can each run their configured number of different tasks.

## Effects, cancellation and recovery

Immediately before dispatching any tool, `ToolExecutor` commits a replay
barrier. Saving an assistant answer also sets this barrier. An interrupted or
failed attempt after that point is never automatically retried. Lease recovery
marks such work `stale`; ordinary failures become `failed`, with review guidance.
This deliberately includes read tools: tool annotations cannot establish that
every remote implementation is idempotent. Inspect the conversation and tool
history before explicitly creating a new task.

This is not an exactly-once guarantee for external systems. Cancellation stops
future work and prevents stale results from being committed, but cannot undo a
dispatched HTTP request or kill a Python handler already running in a thread.
Interrupted tool audits remain available. A completed scheduled occurrence is
a separate execution from the next occurrence; schedules intentionally recur.

The cancel endpoint atomically marks the task terminal and clears its token,
lease and retry time. Local jobs are cancelled immediately; remote workers stop
at the next guard or heartbeat (default 15 seconds). Heartbeat loss also stops
the worker. Shutdown awaits graph/generator cleanup and worker persistence.

## Scheduling policy

Both manual and timed dispatch lock the schedule row. Timed dispatch coalesces
missed occurrences to the latest one and runs it only within the configurable
`misfire_grace_seconds` window (0–86400, default 300). There is no catch-up burst.
A queued/running task from that schedule causes a timed occurrence to be skipped;
Run now returns that existing task. Pausing stops future dispatch; it does not
cancel tasks already queued. Invalid stored schedules are paused independently
so another schedule in the batch can still run.

`last_dispatch_status`, `last_dispatch_at` and `last_dispatch_error` explain the
latest decision. Statuses: `queued`, `missed`, `overlap_skipped`, `invalid`.
`last_run_at` and `last_task_id` refer to an actually queued occurrence, not a
skipped tick. One-time schedules are disabled after dispatch or a missed window.

Daily schedules use IANA timezones. Nonexistent wall times are skipped; ambiguous
times use the first fold only. Comparisons happen in UTC so a fall-back clock
cannot return an occurrence in the past. UI displays this policy in the composer.

## Deployment and verification

Stop all old backend/worker processes before applying migration
`0009_task_fencing`, then start the upgraded backend. Old binaries do not enforce
tokens and must not coexist with new workers. The migration adds nullable token,
deadline/replay markers and scheduling diagnostics, a default attempt budget,
and a partial ready-queue index. Existing queued/running tasks receive a
15-minute deadline at upgrade. Existing running tasks and queued tasks with
tool audit history receive a conservative replay barrier. Back up the database
before deployment; downgrade removes these reliability fields and index.

Focused verification includes `test_task_durability_integration.py` against real
PostgreSQL, plus worker/scheduler/route/tool suites and DST boundary fixtures.
Set `TEST_DATABASE_URL` to a migrated isolated database; each integration case
creates and deletes only its synthetic user and dependent records.

Design references checked on 2026-10-15:

- [PostgreSQL queue locking](https://www.postgresql.org/docs/18/sql-select.html):
  `SKIP LOCKED` supports competing consumers of queue-like tables.
- [Python asyncio cancellation and deadlines](https://docs.python.org/3.14/library/asyncio-task.html):
  bounded async execution, propagated cancellation and awaited cleanup.
- [Temporal activity delivery and idempotency](https://learn.temporal.io/tutorials/python/standalone-activities/):
  repeated delivery requires application-level idempotency. This project applies
  that distinction to its local PostgreSQL queue; it does not add Temporal or
  claim that an arbitrary external effect can be replayed exactly once.
