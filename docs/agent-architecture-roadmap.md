# Agent Architecture and Roadmap

AgentDemo now implements the core capabilities expected from a modern local-first
agent while keeping the existing FastAPI, LangGraph, SSE, and `ToolExecutor`
boundaries stable.

## Implemented Capability Map

| Capability | Current implementation |
| --- | --- |
| Stateful orchestration | LangGraph runtime with retrieval, planning, bounded tool rounds, generation, and persistence. |
| Structured tool use | Local plugins, MCP tools, direct URL/GitHub/Hugging Face/search routes, JSON argument validation, and normalized results. |
| Human oversight | Confirmation gating and confirmed continuation streams for sensitive interactive calls. |
| Safety and audit | Central `ToolExecutor` policy, timeouts, output limits, MCP policy, trace ids, and durable `ToolCall` rows. |
| Background autonomy | Durable agent-task records, row-locked claims, leases/heartbeats, bounded retries, concurrency caps, idempotency, startup recovery, cancellation, results, and task-bound tool audit. |
| Scheduled autonomy | Timezone-aware one-time, fixed-interval, and daily schedules with pause/resume, immediate runs, deterministic occurrence keys, and catch-up burst prevention. |
| Grounding | pgvector-backed RAG with multilingual keyword fallback, source-attributed citations, MCP resource import, and scoped documents. |
| Memory | Recent conversation history plus generated long-term summaries. |
| Observability | Stable SSE events, persisted trace/model metadata, runtime inspector, task event history, and release coverage gates. |
| Quality controls | Scenario-scored agent evaluations, backend lint/coverage, frontend unit tests/build/audit, migration checks, and real API/browser smoke tests. |

## Execution Boundaries

Interactive chat and background tasks use the same `AgentRuntime` graph. The
background worker only adapts graph events into durable task progress and result
records; it does not bypass planning, model routing, RAG, MCP policy, or
`ToolExecutor`. Separate SQLAlchemy sessions isolate task progress writes from
runtime message and tool-audit writes.

The public event contract remains `status`, `plan`, `tool_call`, `tool_result`,
`token`, `done`, and `error`. This keeps the UI and integrations compatible while
the runtime internals continue to evolve.

## Recommended Next Stages

1. Add a durable LangGraph checkpointer so an interrupted graph can resume from
   a node instead of restarting at task granularity.
2. Add an optional shared queue or scheduler leader for globally bounded
   concurrency and efficient wake-ups in horizontally scaled deployments.
3. Expand scenario-based evaluations with curated live answer-grounding and
   citation-quality datasets while keeping the default gate deterministic.
4. Add optional evaluator/reflection nodes only where measurements show a net
   quality gain; keep latency and tool-round budgets explicit.
5. Add notification delivery and per-task tool/model/budget policy profiles to
   the existing scheduled and recurring Agent tasks.
6. Introduce specialized sub-agent delegation only after task isolation,
   cancellation, audit ownership, and budget propagation are defined.

These stages preserve the current local-first design and prioritize reliability
before increasing autonomy.
