# Testing Strategy

This project should stay testable without real model API keys. Prefer fakes for
runtime, RAG, tool execution, and route-level ownership checks, then reserve live
model checks for explicitly skipped opt-in tests.

## Backend Tests

Run all backend tests from the backend directory:

```powershell
cd D:\workplace\AgentDemo\backend
uv sync --locked --extra dev --python 3.12
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pytest --cov=app --cov-report=term-missing --cov-fail-under=80
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m app.evals
.\.venv\Scripts\python.exe -m pip_audit --local --skip-editable
```

CI installs from `backend/uv.lock` with `--locked` and checks Python 3.11,
3.12 and 3.14. The lockfile includes the development extra; CI audits installed
third-party packages and excludes only this editable local project. Audit
network errors fail the gate rather than reporting a clean result.

Useful targeted suites:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_access_token_middleware.py tests/test_tool_routes.py tests/test_task_routes.py
.\.venv\Scripts\python.exe -m pytest tests/test_plugin_registry.py tests/test_tool_executor.py tests/test_read_file_plugin.py
.\.venv\Scripts\python.exe -m pytest tests/test_agent_runtime.py tests/test_model_gateway.py tests/test_model_transport.py
.\.venv\Scripts\python.exe -m pytest tests/test_rag.py tests/test_memory_routes.py
.\.venv\Scripts\python.exe -m pytest tests/test_task_scheduler.py tests/test_agent_task_runner.py tests/test_task_routes.py tests/test_task_schedules.py tests/test_task_schedule_routes.py
```

Backend coverage priorities:

- Auth middleware accepts configured bearer tokens and rejects missing or bad
  tokens.
- User-owned routes reject cross-user conversations, tasks, and memory
  summaries.
- Task status transitions reject invalid moves and terminal-state changes.
- Startup recovery preserves live leases, re-queues abandoned work with attempts
  remaining, and marks exhausted work as `stale`.
- Background agent tasks persist progress/results, renew leases and heartbeats,
  apply bounded retry backoff, bind tool audits to task ids,
  cancel cleanly, and fail safely when interactive confirmation is required.
- Plugin manifests load, disabled tools stay unavailable, and invalid manifests
  do not break registry loading.
- Tool executor validates arguments, enforces confirmation, caps output, records
  audit summaries, and returns stable error shapes.
- Agent runtime emits SSE events in order, persists trace metadata, uses recent
  history, confirms blocked tools through the continuation stream, generates
  memory summaries for long conversations, and includes memory summaries, tool
  observations, and RAG citations in model context.
- RAG search works with vector results, keyword fallback, and conversation
  document prioritization.

## Frontend Tests And Build

Run the production build from the frontend directory:

```powershell
cd D:\workplace\AgentDemo\frontend
npm ci
npm test
npm run build
npm audit --audit-level=high
```

Vitest covers SSE parsing, persisted runtime traces, task rendering helpers, and
schedule descriptions.
The build runs `tsc` and `vite build`; the audit blocks high-severity dependency
findings.

For local manual checks:

```powershell
cd D:\workplace\AgentDemo\frontend
npm run dev
```

Open `http://localhost:5173` while the backend is running on port 8000.

## Fake Model Strategy

Most tests should not call external models.

Use the existing patterns:

- Inject a fake gateway with `route()` and `stream_reply()` into `AgentRuntime`.
  Add `summarize_messages()` when testing memory refresh behavior.
- Inject a fake RAG service with `search()`.
- Use fake sessions that implement only the async methods the unit under test
  needs.
- Monkey-patch `_embed_or_empty()` in RAG tests to force vector or keyword paths.
- Use HTTPX `MockTransport` and fragmented byte streams for gateway retry,
  deadline, cancellation, SSE and embedding validation checks. These exercise
  the real HTTP client path without provider credentials or network access.

`tests/test_agent_runtime_live.py::test_agent_runtime_streams_live_reply` is the
opt-in live test. Pytest excludes the `live` marker by default, including on
machines whose `.env` contains credentials. Run it explicitly when needed:

```powershell
.\.venv\Scripts\python.exe -m pytest -m live
```

It still skips automatically when the configured provider requires a key and
neither `LLM_API_KEY` nor the default DeepSeek compatibility key is available.

## Scenario-Based Agent Evaluations

`python -m app.evals` runs deterministic scenarios without a database, network,
or provider credentials. The checked-in suite covers:

- current-information versus persona-style search intent;
- local file, direct URL, and GitHub repository routing;
- confirmation policy for network fetches;
- removal of raw model tool-protocol markup;
- citation title and source URL preservation in model context;
- English and Chinese keyword extraction for embedding outages.

The command returns a non-zero exit code when its aggregate score falls below
the suite's `minimum_score`. The normal pytest suite executes the same gate, so
the release check cannot accidentally omit it.

## No Real API Key Requirement

Default CI and local QA should pass with empty model API keys:

- Runtime tests use fake gateways.
- RAG fallback tests can force empty embeddings.
- Live model tests must remain excluded unless they are intentionally selected.
- Do not add mandatory tests that depend on OpenAI, DeepSeek, MoleAPI, or any
  other paid or account-bound service.

If a feature needs an external integration test, mark it clearly as live or
integration-only and skip it when the required environment variable is unset.

## Database Strategy

Route and service tests use lightweight fakes for many ownership checks. Durable
approval tests additionally use real PostgreSQL row locks, JSONB matching and
separate sessions to verify restart persistence, owner binding, expiry,
confirmation/cancellation races and rejection of replay. CI runs migrations
and these integration tests on PostgreSQL 16 with pgvector for every Python
version. Model calls remain offline.

To include database tests locally, explicitly set `TEST_DATABASE_URL` to a
dedicated migrated test database. Without it, only integration tests skip.
The fixtures create unique synthetic users and remove only their own rows.

```powershell
$env:TEST_DATABASE_URL = 'postgresql+asyncpg://agent:agent@localhost:5432/agent_test'
$env:DATABASE_URL = $env:TEST_DATABASE_URL
uv run --locked --extra dev alembic upgrade head
uv run --locked --extra dev pytest tests/test_tool_approvals.py tests/test_runtime_limits.py tests/test_context_budget.py -q
```

Browser acceptance for confirmation changes must include: wait for Confirm,
reload, restart the backend, confirm and inspect the final answer/tool history;
create a second approval, cancel, reload and verify it stays cancelled. Check
the composer and Runtime inspector at a mobile viewport as well.

Before release-style verification:

```powershell
cd D:\workplace\AgentDemo\backend
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m pytest
```

Schema changes should include Alembic migrations and focused tests for route or
service behavior that depends on the new fields.

## Documentation Checks

For documentation-only changes, at minimum run:

```powershell
cd D:\workplace\AgentDemo
git diff --check
```

When README commands or setup guidance changes, also sanity-check the relevant
command names against `backend/pyproject.toml`, `backend/.env.example`, and
`frontend/package.json`.

## Release Gate

The commands in this document form the release gate: Ruff, the complete backend
suite with at least 70% coverage, the 100% offline agent-evaluation gate,
frontend unit tests, production build, and a high-severity dependency audit must
all pass before merging `test` into `run`. Repository visibility is an external
release check and should remain public.
