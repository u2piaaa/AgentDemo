# AgentDemo Personal Agent

Local-first personal AI agent scaffold with a FastAPI backend, React + Vite
frontend, PostgreSQL + pgvector storage, local manifest-based plugins, RAG, and
durable foreground and background agent execution.

The current runtime is an executable agent loop rather than a plain chat proxy:
it persists conversation state, retrieves relevant knowledge, plans tool calls,
runs local plugins through an auditable executor, streams events over SSE, and
saves the final assistant message with trace metadata.

## Repository Layout

- `backend/app/main.py`: FastAPI application startup, route mounting, scheduler
  startup, and plugin registry loading.
- `backend/app/agent/runtime.py`: executable LangGraph streaming runtime.
- `backend/app/agent/response_policy.py`: user-visible response sanitization,
  tool inventory, and search/tool fallback policies.
- `backend/app/api/routes`: authenticated API routes for auth, conversations,
  tools, tasks, knowledge, and memory summaries.
- `backend/app/services/model_gateway.py`: OpenAI-compatible embedding and chat
  model routing.
- `backend/app/services/rag.py`: text indexing, vector retrieval, keyword
  fallback, and citation formatting.
- `backend/app/services/plugin_registry.py`: local plugin manifest loader.
- `backend/app/services/tool_executor.py`: argument validation, confirmation
  gate, timeout handling, output limiting, and tool-call audit logging.
- `backend/app/services/agent_task_runner.py`: durable background agent worker
  with persisted progress, leases, heartbeats, bounded retries, cancellation,
  and tool audit binding.
- `backend/app/services/task_scheduler.py`: bounded-concurrency job scheduling,
  due-schedule dispatch, queued-job resume, cancellation, and lease recovery.
- `backend/app/services/task_schedules.py`: timezone-aware one-time, interval,
  and daily schedule calculation with deterministic occurrence idempotency.
- `backend/migrations`: Alembic schema, including `pgvector`, task ownership,
  tool-call audit fields, knowledge documents, and memory summaries.
- `frontend/src/features`: extracted runtime-trace and background-task UI logic.
- `frontend/src`: chat workspace, SSE client, tool panels, citations, auth flow,
  and knowledge upload UI.
- `plugins/read_file`: sample local plugin.
- `docs`: implementation notes, plugin authoring, testing strategy, and final
  regression checklist.

## Executable Agent Architecture

The chat entrypoint is `POST /api/conversations/chat/stream`. The route verifies
the access token, checks conversation ownership, constructs `AgentRuntime`, and
returns an `EventSourceResponse`.

At a high level the runtime does this for each user message:

1. Ensure or create an owned conversation.
2. Load recent user and assistant messages as short-term memory.
3. Auto-title a new conversation from the first user message.
4. Persist the new user message.
5. Retrieve supplemental knowledge with RAG.
6. Plan whether a tool is needed.
7. Execute up to the configured tool round limit.
8. Stream the model response token by token.
9. Persist the assistant response with citations, tool calls, memory summaries,
   trace id, and model route metadata.
10. Refresh the active long-term memory summary when the conversation has grown
    past the configured recent-history window.

Planning combines deterministic routes for direct URLs, GitHub, Hugging Face,
search, and local file requests with model-driven MCP/tool selection. LangGraph
coordinates retrieval, bounded multi-round tool execution, response generation,
and persistence without changing the public SSE contract.

## Tool Call Flow

Tools are discovered from local plugin manifests under `plugins/*/manifest.json`
when the backend starts. A planned tool call flows through these layers:

1. `AgentRuntime` emits a `plan` event.
2. If a tool is selected, the runtime emits `tool_call`.
3. `ToolExecutor` validates arguments against the manifest JSON Schema subset.
4. If `requires_confirmation` is true and the call is not confirmed, execution is
   blocked and audited.
5. The handler runs in a worker thread with the lower of the manifest timeout and
   global timeout.
6. Output is truncated by the manifest and global output policies.
7. A `ToolCall` audit row records input summary, output summary, status, error,
   duration, trace id, user id, conversation id, and optional task id.
8. The runtime emits `tool_result` and adds the observation to the model context.

The direct tool endpoint is `POST /api/tools/{tool_name}/run` with:

```json
{
  "arguments": {},
  "confirmed": false
}
```

The chat runtime first blocks tools that require confirmation and audits that
blocked attempt. When the user confirms in the frontend, it calls
`POST /api/conversations/chat/confirm/stream` with the original message, tool
name, and arguments. The runtime then executes the confirmed tool, streams a new
`tool_call` / `tool_result` / `token` sequence, and persists the continuation
assistant message.

## SSE Events

The stream returns named Server-Sent Events. Each event has JSON in `data`.

| Event | Purpose |
| --- | --- |
| `status` | Runtime phase updates such as `ensure_conversation`, `load_history`, `retrieving_context`, `planning`, `generating`, and message persistence. |
| `plan` | Tool planning decision with `no_tool`, optional `tool_name`, `arguments`, `reason`, and `requires_confirmation`. |
| `tool_call` | Planned tool invocation, arguments, reason, and trace id. |
| `tool_result` | Tool status, output, output summary, error, duration, and trace id. |
| `token` | One streamed model text fragment. |
| `done` | Final metadata including `conversation_id`, `citations`, `tool_calls`, `trace_id`, and `model_route`. |
| `error` | Runtime failure details and trace id when available. |

The frontend parses these events in `frontend/src/api.ts` and renders the active
answer, tool timeline, citations, and final conversation state.

## Task System

Tasks are durable records for longer-running work and UI progress tracking. The
API is mounted at `/api/tasks` and is scoped to the authenticated user.

Supported operations:

- `GET /api/tasks?conversation_id=<uuid>` lists owned tasks.
- `POST /api/tasks` creates a queued task with optional conversation id, trace
  id, and metadata.
- `POST /api/tasks/agent` creates and immediately enqueues an autonomous agent
  task from a prompt, with optional name, conversation id, idempotency key, and
  maximum-attempt policy.
- `GET /api/tasks/{task_id}` returns an owned task.
- `PATCH /api/tasks/{task_id}` updates status, progress, result, error, trace id,
  or metadata.
- `POST /api/tasks/{task_id}/cancel` moves a cancellable task to `cancelled`.

Statuses are `queued`, `running`, `succeeded`, `failed`, `cancelled`, and
`stale`. Terminal states stay terminal. Workers claim execution with a renewable
lease and heartbeat. On startup, a live lease is preserved; an expired lease is
re-queued when attempts remain and becomes `stale` only after its final attempt.
Transient and unknown provider failures use bounded exponential backoff, while
authentication, validation, permission, and confirmation failures stop
immediately. The process-wide worker pool limits concurrent model runs, and retry
delays do not occupy a concurrency slot.

Scheduled Agent execution is mounted at `/api/task-schedules`:

- `GET /api/task-schedules?conversation_id=<uuid>` lists owned schedules.
- `POST /api/task-schedules` creates a timezone-aware `once`, `interval`, or
  `daily` schedule.
- `PATCH /api/task-schedules/{schedule_id}` pauses or resumes a schedule.
- `POST /api/task-schedules/{schedule_id}/run` queues an immediate manual
  occurrence without changing its normal next-run time.

Every scheduled occurrence receives a deterministic, user-scoped idempotency
key, so concurrent scheduler ticks cannot create duplicate work. Missed interval
runs advance to the next future occurrence instead of producing a catch-up
burst. Each background run persists coarse-grained runtime events, the final
answer, citations, model route, timestamps, progress, attempt count, next retry,
heartbeat, and lease. Tool calls retain the task id in the normal `ToolExecutor`
audit path.

Background execution uses separate database sessions for task progress and the
LangGraph runtime. Tools that require interactive confirmation fail safely with
guidance to use chat; they are never auto-approved. The frontend can launch or
schedule a prompt from the composer, pause/resume or immediately run schedules,
and renders progress, retry state, result text, failure details, and cancellation
controls in the Runtime inspector.

See [Agent Architecture and Roadmap](docs/agent-architecture-roadmap.md) for the
implemented capability map and next-stage priorities.

## RAG And Memory

Knowledge ingestion is mounted at `/api/knowledge`.

- `POST /api/knowledge/documents` indexes provided text.
- `POST /api/knowledge/documents/upload` accepts TXT, Markdown, and text-based
  PDF uploads.
- Documents are chunked with overlap, embedded through `ModelGateway`, and stored
  in `knowledge_chunks`.
- Search prefers vector similarity when embeddings are available.
- If embedding is unavailable, search falls back to keyword matching.
- Conversation-scoped documents are prioritized over global documents.
- Citations include document id, title, chunk index, content, source type, score,
  retrieval method, and citation metadata.

Conversation memory has two layers:

- Short-term memory is the recent conversation history loaded by
  `AgentRuntime._load_recent_history`.
- Long-term memory summaries have API support under `/api/memory/summaries` for
  listing, reading, disabling, and deleting summaries owned through their
  conversation.
- `AgentRuntime` injects active memory summaries into model context and refreshes
  the active summary after long conversations. Summary generation is best-effort:
  if model credentials are missing or the provider fails, the chat response still
  succeeds and memory refresh is skipped for that turn.

## Local Setup

Prerequisites:

- Python 3.11+
- Node.js 20+
- PostgreSQL 16 with pgvector available

Install backend dependencies:

```powershell
cd D:\workplace\AgentDemo\backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .[dev]
Copy-Item .env.example .env
```

This repository can run PostgreSQL from a local binary package without Docker:

```powershell
cd D:\workplace\AgentDemo
New-Item -ItemType Directory -Force .local\postgres | Out-Null
Expand-Archive -LiteralPath downloads\postgresql-16.14-1-windows-x64-binaries.zip -DestinationPath .local\postgres -Force
.\.local\postgres\pgsql\bin\initdb.exe -D .local\postgres\data -U agent -A trust --encoding=UTF8 --locale=C
.\.local\postgres\pgsql\bin\pg_ctl.exe -D .local\postgres\data -l .local\postgres\postgres.log start
.\.local\postgres\pgsql\bin\createdb.exe -U agent agent_demo
```

If pgvector is not bundled with the PostgreSQL zip, install a PostgreSQL 16
Windows pgvector package by copying its `lib`, `share\extension`, and
`include\server\extension\vector` contents into `.local\postgres\pgsql`, then:

```powershell
.\.local\postgres\pgsql\bin\psql.exe -U agent -d agent_demo -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

Run migrations and start the backend:

```powershell
cd D:\workplace\AgentDemo\backend
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\uvicorn.exe app.main:app --reload --host 0.0.0.0 --port 8000
```

The backend exposes two unauthenticated operational probes:

- `GET /api/health` is a lightweight process liveness check.
- `GET /api/health/ready` verifies the database connection and returns HTTP 503
  with an unavailable status when PostgreSQL cannot be reached.

Install and start the frontend:

```powershell
cd D:\workplace\AgentDemo\frontend
npm install
npm run dev
```

For an isolated worktree or a backend on a non-default port, set the Vite proxy
target in an ignored `frontend/.env.local` file before starting the frontend:

```text
VITE_API_TARGET=http://127.0.0.1:8001
```

The default remains `http://localhost:8000` when `VITE_API_TARGET` is unset.

Open `http://localhost:5173` on this machine, or
`http://<this-machine-ip>:5173` from another device on the same network. The
Vite dev server proxies `/api` to the local backend.

For temporary public internet access without router port forwarding, run a tunnel
to the frontend:

```powershell
.\.local\cloudflared\cloudflared.exe tunnel --url http://localhost:5173
```

Open the generated `https://*.trycloudflare.com` URL and enter the
`AGENT_ACCESS_TOKEN` configured in `backend\.env`. In the local bootstrap used by
this project, the generated access token may also be stored in
`.local\agent-access-token.txt`.

## Model Configuration

The runtime uses OpenAI-compatible HTTP APIs:

- Embeddings: `OPENAI_BASE_URL`, `OPENAI_API_KEY`,
  `OPENAI_EMBEDDING_MODEL`
- Agent chat: `LLM_BASE_URL`, `DEEPSEEK_API_KEY`, `LLM_CHAT_MODEL`

Default development settings are in `backend/.env.example`. Automated tests and
offline agent evaluations use fakes and do not need real API keys. Paid live
checks are excluded by default even when a local `.env` contains credentials;
run `python -m pytest -m live` explicitly when validating a configured provider.

## Plugin Authoring

Local plugins live under `plugins/<plugin_name>` with a `manifest.json` and a
Python entrypoint. See [Plugin Authoring](docs/plugin-authoring.md) for manifest
fields, supported JSON Schema validation, permissions, confirmation behavior,
safe path rules, output summaries, and plugin test patterns.

AgentDemo v2 also maps local plugins to MCP-compatible schemas and can register
configured external MCP tools through the same executor path.

## MCP Compatibility

AgentDemo can act as an MCP-compatible server for local tools and as an MCP
client for configured external servers. Server mode exposes local tools through
`backend/app/mcp/server.py`; client mode loads configured servers through
`backend/app/mcp/config.py` and `backend/app/mcp/client.py`.

MCP calls never bypass the local tool executor. Tool calls still use argument
validation, confirmation, audit rows, trace IDs, user/task binding, and frontend
timeline events. The default policy is local-only; remote MCP exposure requires
explicit configuration.

MCP resources can be used as runtime context or imported as RAG documents with
`source_type=mcp_resource` and `source_uri` set to the MCP URI. MCP prompts can
be selected by name as runtime context. The frontend shows MCP servers, tools,
resources, prompts, provider labels, and task events.

See [MCP Compatibility](docs/mcp-compatibility.md) for the full mapping and
security model.

## Tests

Backend:

```powershell
cd D:\workplace\AgentDemo\backend
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pytest --cov=app --cov-fail-under=70
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m app.evals
```

The deterministic evaluation suite scores tool routing, confirmation policy,
user-visible protocol safety, citation attribution, and multilingual retrieval.
Its current release gate is 100%; scenarios live in
`backend/app/evals/scenarios.json`.

Frontend:

```powershell
cd D:\workplace\AgentDemo\frontend
npm ci
npm test
npm run build
npm audit --audit-level=high
```

Lightweight documentation check:

```powershell
cd D:\workplace\AgentDemo
git diff --check
```

See [Testing Strategy](docs/testing-strategy.md) and
[Final Regression Checklist](docs/final-regression-checklist.md) for the full QA
handoff checklist.

## Known Limitations

- Background workers are in-process. Queued work resumes after a clean restart,
  and database row locks plus leases prevent duplicate claims, but enforcing one
  aggregate concurrency limit across many instances still needs a shared queue.
- Confirmation-required tools remain an interactive-chat workflow; background
  tasks deliberately do not pause indefinitely for approval.
- Schedules currently expose in-app state but do not yet deliver email, desktop,
  or webhook notifications.
- LangGraph coordinates execution, but durable node-level checkpoint/resume is a
  future capability beyond the current task-level persistence.
