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
5. Surface direct-URL/tool confirmation early, or retrieve supplemental knowledge with RAG.
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
3. `ToolExecutor` validates arguments against JSON Schema 2020-12, including
   nested constraints and local references. External schema retrieval is disabled.
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
blocked attempt. It saves the pending plan and continuation context in an
assistant trace so confirmation survives a reload or backend restart. When the
user confirms in the frontend, it calls
`POST /api/conversations/chat/confirm/stream` with the original message, tool
name, arguments, and the server-issued `approval_id`. An owner-bound row lock
atomically consumes the approval before any tool effect; expired, mismatched,
cancelled or already-consumed approvals return HTTP 409. Legacy clients without
an ID must exactly match a pending stored plan.

The runtime restores earlier observations without replaying earlier tools,
then executes the approved tool through `ToolExecutor`, streams a new
`tool_call` / `tool_result` / `token` sequence, and persists the continuation
assistant message. `POST /api/conversations/chat/confirm/cancel` accepts
`conversation_id` and `approval_id` and persists cancellation. Interrupted
continuations remain consumed: inspect tool history before requesting the work
again, because an external effect may already have completed.

Search enrichment follows the same confirmation policy: each selected webpage
is saved as a pending Fetch MCP approval. Confirmation resumes the remaining
queue without repeating the search or previously completed fetches. Cancelling
the pending approval stops that enrichment chain.

## MCP Connections

The client uses the official MCP Python SDK 2.3 and negotiates the stable
`2026-07-28` protocol, with automatic fallback to legacy initialization through
`2025-11-25`. Standard JSONL, Streamable HTTP and legacy HTTP+SSE use the SDK.
Existing nonstandard `content-length` subprocess configs retain a compatibility
adapter. Remote tools still run through `ToolExecutor`; protocol migration does
not grant additional execution permissions.

Copy `mcp.servers.example.json` to the ignored `mcp.servers.json` and configure
only the services you use. Header credentials must use `${ENVIRONMENT_VARIABLE}`
references. Subprocesses inherit OS essentials plus explicitly mapped variables.

Fetch MCP currently depends on SDK 1.x and needs a separate environment:

```powershell
uv sync --project mcp/fetch --locked
```

The Windows example points to `mcp\\fetch\\.venv\\Scripts\\python.exe`.
On Linux/macOS use `mcp/fetch/.venv/bin/python`. When upgrading an existing
installation, update only the Fetch server's `command` to this interpreter
before upgrading the backend environment. Keep the existing arguments and
environment mappings. `mcp/fetch/uv.lock` pins the independent dependency set;
do not install `mcp-server-fetch` in `backend/.venv`.

GitHub MCP 2.0.2 has been verified against this client with the existing read-only
tool policy, including its `2026-07-28` structured outputs. For a native install,
use the [official release](https://github.com/github/github-mcp-server/releases/tag/v2.0.2),
verify the published checksum, and update the ignored config's `command` path.
Keep downloaded service binaries under `.local`, outside Git.

Per-server config supports `protocol_mode` (`auto`, `legacy`, `2026-07-28`),
`request_timeout_seconds` (30), `discovery_timeout_seconds` (15),
`discovery_cache_seconds` (60), `discovery_max_pages` (20),
`discovery_max_items` (1,000), and `max_response_bytes` (2 MiB). The root
`discovery_concurrency` defaults to 4. HTTP bodies are bounded before parsing;
stdio results are bounded after SDK parsing (legacy framed messages before
parsing). Only catalog metadata is cached, never tool execution results.

Runtime → Tools shows the last successful protocol negotiation and check time.
`ready` means the latest connection succeeded; it is not a persistent socket or
a continuous health guarantee. `configured` only describes static config,
`mock` identifies explicit fixtures, and `error` reports a failed check.
**Refresh MCP** calls authenticated `POST /api/mcp/refresh`, invalidates catalog
caches, and atomically replaces registered remote tools. One failed server does
not hide tools from healthy servers.

Structured MCP content and resource links are retained, output schemas are
validated, binary media is omitted from text context, and nested output is
bounded. `isError` remains a failed result in the UI, model context and audit.
Interrupted execution is audited as `interrupted`; its external outcome can be
unknown, so the app does not automatically replay it.

Runtime budgets are configurable in `backend/.env`: `AGENT_RUN_TIMEOUT_SECONDS`
(180), `AGENT_RETRIEVAL_TIMEOUT_SECONDS` (12), `AGENT_CONTEXT_MAX_CHARS` (48,000)
and `AGENT_APPROVAL_TTL_SECONDS` (86,400). The character budget includes the
current request, recent history and supplemental model context; it is not a
model-specific token estimate. Trimming keeps recent history and tool results,
records budget diagnostics in message metadata, and never silently truncates
the current request. A retrieval timeout reports progress and continues with
available context. A run timeout ends the stream with an error.

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
- Paragraph-aware chunks preserve code indentation and normalized text offsets.
  Title and section context enrich embedding and lexical inputs while quoted
  evidence stays verbatim. Existing documents can be upgraded with the **Reindex
  document** control or `POST /api/knowledge/documents/{id}/reindex`.
- Semantic and multilingual lexical candidates are always combined with reciprocal
  rank fusion (RRF). SQL filters and ranks lexical matches before limiting them.
  Equal similarity scores share a rank; scope breaks relevance ties.
- Local MMR selection reduces redundant evidence. Optional Jina-compatible
  listwise reranking supports `jina-reranker-v3.5` and self-hosted endpoints;
  timeout or malformed output preserves local ranking. See [RAG configuration](docs/rag-memory.md).
- All retrieval paths enforce owner and conversation/global scope. Missing or slow
  embeddings retain keyword evidence; the inspector shows indexing and retrieval modes.
- Citations include document id, title, chunk index, content, source type, score,
  retrieval method, and citation metadata.

Conversation memory has two layers:

- Short-term memory is the recent conversation history loaded by
  `AgentRuntime._load_recent_history`.
- Long-term memory summaries have API support under `/api/memory/summaries` for
  listing, reading, disabling, and deleting summaries owned through their
  conversation.
- Incremental compaction processes only older, unprocessed messages, up to 50
  messages / 16,000 input characters in 15 seconds by default. A durable cursor
  tracks partial large messages too. Conversation row locking prevents competing
  workers from overwriting the summary; failure/cancellation never consumes input.
- Disabling/deleting a summary preserves the processed-history boundary so those
  facts are not automatically regenerated. Previously disabled legacy summaries
  are respected. Summary generation remains best-effort after the answer is saved.

## Local Setup

Prerequisites:

- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.11.5+ (CI uses 0.13.1)
- Node.js 20+
- PostgreSQL 16 with pgvector available

Install backend dependencies:

```powershell
cd D:\workplace\AgentDemo\backend
uv sync --locked --extra dev --python 3.12
Copy-Item .env.example .env
```

`backend/uv.lock` is the reproducible backend dependency baseline. Use
`uv sync --locked --extra dev` in every worktree; each receives its own ignored
`.venv`. The `--locked` flag rejects stale dependency metadata. Upgrade packages
deliberately with `uv lock --upgrade-package <name>`, then run the quality gates
before committing the lockfile. External MCP servers should use their own
environment rather than installing untracked packages into the app environment.

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
npm ci
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
- Agent chat: `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_CHAT_MODEL`.
  The default DeepSeek provider also accepts the existing `DEEPSEEK_API_KEY`.

The gateway records the configured provider in trace metadata. For a compatible
local model endpoint, explicitly set its provider label, URL/model, and
`LLM_API_KEY_REQUIRED=false` if it does not use authentication. Other providers
never inherit the DeepSeek key automatically.

`LLM_REQUEST_TIMEOUT_SECONDS` is the total request deadline, including retries
and backoff. Connections have a separate `LLM_CONNECT_TIMEOUT_SECONDS` bound.
Transient network failures and HTTP 408/429/500/502/503/504 are retried at most
`LLM_MAX_RETRIES` times, honoring bounded `Retry-After` delays. Once response
text has been yielded, the gateway never replays a request. Broken, incomplete,
or provider-error streams fail instead of being saved as complete answers.

Generation is bounded by `LLM_MAX_OUTPUT_TOKENS`. Use
`LLM_OUTPUT_TOKEN_PARAMETER` to select the token-limit field supported by your
compatible provider. `LLM_REASONING_EFFORT` is optional and only sent when set;
when present it replaces the default temperature setting. Configure a value
supported by your chosen model. Token-limit exhaustion is reported as an error.
Embedding requests use bounded batches, retain input order, and validate vector
count, indices, finite values and configured dimensions before database writes.

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
.\.venv\Scripts\python.exe -m pytest --cov=app --cov-fail-under=80
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m app.evals
.\.venv\Scripts\python.exe -m pip_audit --local --skip-editable
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

The [October 2026 module upgrade ledger](docs/upgrade-2026-10.md) records the
current modernization sequence, acceptance checks, and release evidence.

## Known Limitations

- Background workers are in-process. Queued work resumes after a clean restart,
  and database row locks plus leases prevent duplicate claims, but enforcing one
  aggregate concurrency limit across many instances still needs a shared queue.
- Confirmation-required tools remain an interactive-chat workflow; background
  tasks deliberately do not pause indefinitely for approval.
- Schedules currently expose in-app state but do not yet deliver email, desktop,
  or webhook notifications.
- LangGraph coordinates execution, but durable node-level checkpoint/resume is a
  future capability beyond the current task and tool-approval persistence.
