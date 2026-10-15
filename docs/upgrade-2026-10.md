# AgentDemo module upgrade — October 2026

This is the execution ledger for the requested complete module-by-module
upgrade. Each iteration is implemented and checked on `test`, then released to
`run` and pushed. Large refactors use an isolated feature worktree and enter
`test` only after acceptance. Existing SSE events and the `ToolExecutor` safety
and audit boundary remain compatibility requirements.

## Baseline (2026-10-15, Asia/Hong_Kong)

- Both release branches start at `f640e8e`, synchronized with the public
  `u2piaaa/AgentDemo` repository. Both worktrees are clean.
- Backend: 215 passed, 1 live test deselected. Frontend: 13 passed; build passes.
- Frontend audit reports one high-severity `source-map-js` advisory.
- Backend dependencies have no lockfile and outdated minimum versions.
- Ports 8000, 5173 and 5432 are initially idle. Release state does not imply
  that the app is serving the upgraded code.

## Iterations and acceptance

| # | Module | Upgrade focus | Acceptance | State |
| --- | --- | --- | --- | --- |
| 1 | Dependencies and quality gates | Current stable backend packages, reproducible lockfile, Python compatibility matrix, dependency audits, baseline vulnerability fix | Clean locked install; full backend tests, coverage, evaluations and lint; frontend tests/build/audit | Released: `8895a7a` |
| 2 | Model gateway | Provider-neutral configuration, bounded retries/timeouts, robust streaming and embedding validation | Deterministic transport fault tests; no duplicated streamed tokens; existing model/runtime tests | Released: `4535e3c` |
| 3 | Agent runtime and conversations | Explicit execution/context budgets, cancellation and reliable continuation while preserving graph/SSE semantics | Runtime/route regression tests, protocol sanitization, real confirmation flow | Planned |
| 4 | Tools, search and MCP | Current stable MCP protocol capabilities, accurate connection/error state, bounded discovery and execution | MCP/security/executor/search suites; real tool confirmation and history | Planned |
| 5 | RAG and memory | Hybrid ranking and bounded context with reliable source attribution and scoped retrieval | Retrieval quality fixtures; ownership and memory tests; real document/query flow | Planned |
| 6 | Background tasks and schedules | Deadline/recovery/cancellation correctness and inspectable execution budgets | Worker/scheduler/timezone/concurrency tests; real scheduled task flow | Planned |
| 7 | Authentication and API operations | Current password hashing with legacy migration, bounded operational checks and safe defaults | Real PostgreSQL register/login writes; ownership/auth/health tests | Planned |
| 8 | Frontend workspace | Current stable React/Vite toolchain, resilient streaming and accessible responsive task/runtime controls | Unit tests/build/audit; desktop/mobile browser chat, tools and inspector | Planned |
| 9 | Integration and release | Reconcile docs/config/migrations and validate the combined app | Full quality gates, migrations, browser acceptance, both remote branches and live process verification | Planned |

Each module gets a substantive, measured improvement. Preview features and
infrastructure migrations are adopted only when they solve an observed need
without losing local-first operation. Package versions are checked against
official registries; a feature is not described as implemented until its
acceptance checks pass.

## Iteration 1 — dependency and quality foundation

Official sources checked on 2026-10-15:

- [uv lock and sync](https://docs.astral.sh/uv/concepts/projects/sync/)
- [uv GitHub Actions integration](https://docs.astral.sh/uv/guides/integration/github/)
- [PyPI package metadata](https://docs.pypi.org/api/json/), queried for each
  direct dependency; stable releases are used without prerelease flags.
- [SQLAlchemy asyncio installation](https://docs.sqlalchemy.org/en/21/faq/installation.html):
  declare the `asyncio` extra explicitly so greenlet is installed with 2.1.

Implemented: all direct backend libraries updated to the current stable PyPI
releases with major-version bounds; `uv.lock` records the complete resolution.
SQLAlchemy explicitly includes asyncio/greenlet and Windows explicitly includes
timezone data. CI uses locked installs on Python 3.11/3.12/3.14 and audits both
backend and frontend packages. `source-map-js` is patched to 1.2.2.

Local acceptance on Python 3.12.8 / Windows:

- Clean independent `uv sync --locked --extra dev`: passed.
- Backend: 215 passed, 1 live test deselected; coverage 81.13% (80% gate).
- Ruff: passed. Offline agent evaluation: 12/12, 100%.
- Backend audit: no known vulnerabilities (only the editable app excluded).
- Fresh `npm ci`, 13 frontend tests, production build and npm audit: passed;
  no known frontend vulnerabilities.
- All eight Alembic migrations applied to an isolated PostgreSQL 16 test
  database. App lifespan, readiness, actual register/login/session and
  conversation writes passed with the upgraded packages.
- Existing Windows pytest temporary-directory cleanup warnings remain;
  they do not represent failing application tests.

[GitHub quality run 38695466419](https://github.com/u2piaaa/AgentDemo/actions/runs/38695466419)
passed all three Python versions and frontend checks. Commit `8895a7a` was
fast-forwarded from `test` into `run` and both branches were pushed. Runtime
model/provider calls remain excluded from this foundation acceptance.

## Iteration 2 — model gateway

Official references checked on 2026-10-15:

- [HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/) distinguish
  network inactivity from an operation deadline. The gateway now has both.
- [DeepSeek chat API](https://api-docs.deepseek.com/api/create-chat-completion/)
  defines streamed completion markers, token limits and reasoning effort.
- [DeepSeek errors](https://api-docs.deepseek.com/quick_start/error_codes/) inform
  the distinction between transient and permanent provider failures.
- [SSE parsing standard](https://html.spec.whatwg.org/multipage/server-sent-events.html#parsing-an-event-stream)
  defines multiline data fields, comments and blank-line event dispatch.

Implemented: configurable provider labels/credentials with legacy DeepSeek
fallback; explicit opt-in for keyless local endpoints; configurable output and
reasoning budgets; bounded transient retries with jitter and Retry-After;
total operation deadlines; cancellation propagation; no replay after emitted
text; strict SSE completion/error handling; bounded, ordered, validated
embedding batches. Existing runtime and tool-execution contracts are unchanged.

Acceptance:

- 53 new transport fault/validation cases; full backend suite: 268 passed,
  1 live test deselected. Coverage: 82.56%; gateway coverage: 88%.
- Ruff, lockfile check and 12/12 offline Agent evaluations: passed.
- Real browser against the upgraded test worktree on ports 8001/5174 and an
  isolated PostgreSQL database: registration, chat, final answer persistence,
  immediate MCP confirmation, confirmed execution and tool history passed.
- A local HTTP model fixture intentionally returned 503 on its first chat
  request, then 200; the browser received one complete answer. This checks the
  actual transport path but does not certify a commercial provider's live API.
- Screenshot: ignored `.local/model-gateway-browser.jpg` in the test worktree.

Browser testing also reproduced an existing runtime defect: a blocked tool
confirmation disappears after a page reload because the blocked graph path
does not persist an assistant trace. Fix this first in iteration 3, including
refresh/replay regression checks. It is not a model transport regression.

[GitHub quality run 38697039472](https://github.com/u2piaaa/AgentDemo/actions/runs/38697039472)
passed all Python versions and frontend checks. Commit `4535e3c` was
fast-forwarded into `run` and both release branches were pushed.

## Next iteration handoff

Iteration 3 starts from the verified `4535e3c` implementation baseline. Reproduce
the lost-confirmation issue by requesting a direct URL, waiting for Confirm,
then reloading. The current blocked path in `agent/graph.py` skips
`save_assistant_message`; the frontend only retains the draft in memory.
Persist approval state, resolve it durably after a decision, and prevent stale
or duplicate approvals from repeating side effects. Preserve all executor
checks and avoid introducing checkpoint replay of already-executed tools.

The isolated acceptance database is `agent_upgrade_20261015`. Local HTTP model
and MCP fixtures live under ignored `.local` in the test worktree. The browser
test account and fixture replies belong only to that database. Do not copy
fixture settings or synthetic model answers into the user's normal database.
