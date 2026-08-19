# Testing Strategy

This project should stay testable without real model API keys. Prefer fakes for
runtime, RAG, tool execution, and route-level ownership checks, then reserve live
model checks for explicitly skipped opt-in tests.

## Backend Tests

Run all backend tests from the backend directory:

```powershell
cd D:\workplace\AgentDemo\backend
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pytest --cov=app --cov-report=term-missing --cov-fail-under=70
.\.venv\Scripts\python.exe -m ruff check app tests
```

Useful targeted suites:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_access_token_middleware.py tests/test_tool_routes.py tests/test_task_routes.py
.\.venv\Scripts\python.exe -m pytest tests/test_plugin_registry.py tests/test_tool_executor.py tests/test_read_file_plugin.py
.\.venv\Scripts\python.exe -m pytest tests/test_agent_runtime.py tests/test_model_gateway.py
.\.venv\Scripts\python.exe -m pytest tests/test_rag.py tests/test_memory_routes.py
.\.venv\Scripts\python.exe -m pytest tests/test_task_scheduler.py tests/test_agent_task_runner.py tests/test_task_routes.py
```

Backend coverage priorities:

- Auth middleware accepts configured bearer tokens and rejects missing or bad
  tokens.
- User-owned routes reject cross-user conversations, tasks, and memory
  summaries.
- Task status transitions reject invalid moves and terminal-state changes.
- Startup task recovery marks old `running` tasks as `stale`.
- Background agent tasks persist progress/results, bind tool audits to task ids,
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

Vitest covers SSE parsing, persisted runtime traces, and task rendering helpers.
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
- Keep direct `ModelGateway` unit tests focused on routing and message assembly,
  not network behavior.

`tests/test_agent_runtime_live.py::test_agent_runtime_streams_live_reply` is the
opt-in live test. It skips automatically when `DEEPSEEK_API_KEY` is empty.

## No Real API Key Requirement

Default CI and local QA should pass with empty model API keys:

- Runtime tests use fake gateways.
- RAG fallback tests can force empty embeddings.
- Live model tests must remain skipped unless keys are intentionally configured.
- Do not add mandatory tests that depend on OpenAI, DeepSeek, MoleAPI, or any
  other paid or account-bound service.

If a feature needs an external integration test, mark it clearly as live or
integration-only and skip it when the required environment variable is unset.

## Database Strategy

Route and service tests currently use lightweight fakes for many ownership and
state-transition checks. Full local runs still need PostgreSQL + pgvector for
migrations and end-to-end manual testing.

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
suite with at least 70% coverage, frontend unit tests, production build, and a
high-severity dependency audit must all pass before merging `test` into `run`.
Repository visibility is an external release check and should remain private.
