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
| Background autonomy | Durable agent-task records, progress/events, startup recovery, cancellation, results, and task-bound tool audit. |
| Grounding | pgvector-backed RAG with keyword fallback, citations, MCP resource import, and scoped documents. |
| Memory | Recent conversation history plus generated long-term summaries. |
| Observability | Stable SSE events, persisted trace/model metadata, runtime inspector, task event history, and release coverage gates. |
| Quality controls | Backend lint/coverage, frontend unit tests/build/audit, migration checks, and real API/browser smoke tests. |

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
2. Move background dispatch to a queue with leases, heartbeats, retry policy,
   idempotency keys, and concurrency limits for multi-process deployments.
3. Add scenario-based agent evaluations for answer grounding, tool selection,
   confirmation policy, citation quality, and regression scoring.
4. Add optional evaluator/reflection nodes only where measurements show a net
   quality gain; keep latency and tool-round budgets explicit.
5. Add scheduled and recurring agent tasks with timezone-aware triggers,
   notification delivery, and per-task policy profiles.
6. Introduce specialized sub-agent delegation only after task isolation,
   cancellation, audit ownership, and budget propagation are defined.

These stages preserve the current local-first design and prioritize reliability
before increasing autonomy.
