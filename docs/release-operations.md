# Local release operations

Use `test` for development, then merge checked commits into `run` and push both
branches. The normal checkout serves 8000/5173; acceptance worktrees use other
ports and a separate database. Git state and a live process are separate facts.

## Before updating the running app

1. Check `git worktree list`, `git branch --show-current` and
   `git status --short --branch` in each intended checkout. Do not reset another
   worktree to make its branch match.
2. Inspect `Get-NetTCPConnection -State Listen -LocalPort 8000,5173` and the owning
   process command lines. Stop the identified backend and its workers before
   applying task/session migrations. Do not terminate unrelated processes.
3. Back up the configured PostgreSQL database with `pg_dump -Fc`. Keep that dump
   and local configuration backups under ignored `.local/backups`, verify the
   archive with `pg_restore --list`, and record its checksum. Never commit them.
4. Note schema revision and row counts for users, conversations/messages,
   documents/chunks, memories, tasks/schedules and tool audits. Compare them after
   migration before adding any acceptance data.

## Install and migrate

From the stable checkout, with Python 3.11+ and Node 24 LTS available:

```powershell
cd backend
uv sync --locked --extra dev
uv run --locked --extra dev alembic current
uv run --locked --extra dev alembic upgrade head
uv pip check
cd ../frontend
npm ci
npm test
npm run build
npm audit --audit-level=high
```

The October upgrade requires schema `0010_auth_sessions`. Migration 0009 adds
worker fencing and marks legacy uncertain execution as replay-blocked. Migration
0010 preserves existing password hashes and assigns old sessions their original
creation time plus seven days; it does not renew expired tokens. Old browser
sessions may therefore require a normal login.

Keep existing model, embedding and search credentials in the ignored local
environment. The default reranker is local MMR; optional API reranking requires
its own configuration. Do not copy a test fixture URL or mock search provider
into the stable environment. Remote origins must be explicitly configured;
the standard loopback CORS expression is in `backend/.env.example`.

Fetch MCP has its own locked environment under `mcp/fetch`. GitHub MCP uses the
configured official binary and read-only tool selection. Its environment
reference must resolve when the backend starts. If intentionally reusing the
already-authenticated GitHub CLI, inject `gh auth token --hostname github.com`
into `GITHUB_PERSONAL_ACCESS_TOKEN` in that backend process only; do not print or
save the token. Missing credentials should remain visible as a server error.

## Start and verify

Run each process in its own terminal, from the indicated directory:

```powershell
# backend
uv run --locked --extra dev uvicorn app.main:app --host 127.0.0.1 --port 8000
# frontend (Node 24)
npm run dev -- --host 127.0.0.1 --port 5173
```

This is a loopback local service, not public production hosting. Verify all of:

- `/api/health/ready` reports database and schema ready, including through the
  frontend proxy. Readiness alone does not verify authentication or a model.
- Real registration/login and conversation writes work. Use a separate synthetic
  acceptance account, and never inspect another account's message contents.
- The real browser renders a streamed answer and preserves it after reload.
  Check tool approval, history, final answer, sources and the task inspector.
- MCP reports the actual negotiated protocol and registered tool count for each
  server. A running process is not proof of successful capability discovery.
- No raw tool protocol appears in assistant text. Test optional provider failures
  with labelled fixtures, and distinguish those checks from a real provider call.
- Both pushed branch SHAs match the intended release, CI is successful, and the
  port owners use the stable checkout. Record only nonsensitive release evidence.

Use [Final regression checklist](final-regression-checklist.md) for full acceptance.
CI runs Python 3.11/3.12/3.14, real PostgreSQL integration tests, dependency audits,
the 80% backend coverage gate and Node 24 frontend checks.

## Recovery

Preserve the backup and upgraded database if startup fails. Keep old workers
stopped while diagnosing. Prefer a forward fix; do not blindly downgrade the
schema or restore over a database that has accepted new user writes. Rehearse
restore into a separate database first, then pair the intended schema snapshot
with its compatible code revision. Configuration rollback must preserve current
credentials and explicit origin policies.
