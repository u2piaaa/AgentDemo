# Final Regression Checklist

Use this checklist before merging tested work into `run` and pushing release
branches. Record command results in the handoff notes.

## Branch And Status

- Confirm development happened on the intended feature branch or worktree.
- Confirm `test` contains the integrated feature set being released.
- Confirm `run` is not modified until final merge verification is complete.
- Run:

```powershell
git status --short --branch
git log -1 --oneline
```

Expected: clean or intentionally documented local changes only.

## Backend

Run migrations against the local development database:

```powershell
cd D:\workplace\AgentDemo\backend
.\.venv\Scripts\alembic.exe upgrade head
```

Run backend tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pytest --cov=app --cov-report=term-missing --cov-fail-under=70
.\.venv\Scripts\python.exe -m ruff check app tests
```

Minimum targeted fallback if time is tight:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_access_token_middleware.py tests/test_tool_routes.py tests/test_task_routes.py tests/test_plugin_registry.py tests/test_tool_executor.py tests/test_agent_runtime.py tests/test_rag.py tests/test_memory_routes.py tests/test_mcp_security.py tests/test_mcp_tooling.py
```

Expected: tests pass. Live model tests may skip when API keys are absent.

## Frontend

Install from lockfile and build:

```powershell
cd D:\workplace\AgentDemo\frontend
npm ci
npm test
npm run build
```

Run audit and record the current result:

```powershell
npm audit --audit-level=high
```

Expected: unit tests and build pass, and the audit has no high-severity findings.

## Git Hygiene

Run from repository root:

```powershell
cd D:\workplace\AgentDemo
git diff --check
git status --short
git status --ignored --short
```

Review `.gitignore` still excludes:

- `.env`
- `.venv/`
- `.local/`
- `downloads/`
- `node_modules/`
- `dist/`
- logs and Python cache/build outputs

## Sensitive File Check

Before pushing, inspect tracked files and pending changes for obvious secrets or
runtime data:

```powershell
git ls-files | Select-String -Pattern '\.env$|\.local|downloads|node_modules|dist|\.venv|\.log$'
git diff --cached --name-only
git diff --cached --check
git diff --cached | Select-String -Pattern 'api[_-]?key|secret|token|password|BEGIN (RSA|OPENSSH|PRIVATE) KEY' -CaseSensitive:$false
```

Expected: no tracked runtime data, downloaded binaries, dependency folders,
logs, build outputs, private keys, or real credentials. Example env files may
contain empty placeholders only.

## Manual Acceptance

With backend and frontend running:

1. Open `http://localhost:5173`.
2. Register or log in with a local account and access token as configured.
3. Start a new chat and verify streaming tokens appear.
4. Ask a normal question and verify no tool is required.
5. Ask to read or summarize `README.md` and verify `plan`, `tool_call`,
   `tool_result`, and final answer are shown.
6. Upload a TXT or Markdown document and verify citations appear when asking
   about its content.
7. Launch a background agent task from the composer and verify queued/running
   progress, persisted events, result text, and task-bound tool audit data.
8. Cancel a running background task and verify it becomes `cancelled` with a
   completion timestamp.
9. Restart the backend with a manually created `running` task and verify startup
   recovery marks it `stale`.
10. Confirm a tool requiring confirmation is shown as blocked, then approve it
    and verify a continuation stream runs the tool and writes a follow-up
    assistant response.
11. Continue a long conversation past the configured memory window and verify an
    active memory summary is generated or updated when model credentials are
    configured. Without credentials, verify chat still succeeds and memory
    refresh is skipped.
12. Verify no MCP config still leaves normal chat, RAG, tasks, and local tools
    working.
13. Verify the MCP server adapter lists `read_file`, `list_dir`, and
    `search_files`.
14. Verify MCP `read_file` cannot read outside the workspace.
15. Verify a fake MCP tool appears in the tool list, can be planned by runtime,
    writes a tool-call audit, and writes task metadata events.
16. Verify MCP resource import creates a `mcp_resource` knowledge document and
    citations show the MCP `source_uri`.
17. Verify the frontend MCP panel handles empty, connected, and error states
    without breaking chat.

## Release Notes To Record

- Exact backend and frontend test counts and coverage.
- Migration head and real background-task smoke results.
- GitHub repository visibility and pushed `test` / `run` commit ids.
- Browser viewport, console, and overflow checks.
