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
| 3 | Agent runtime and conversations | Explicit execution/context budgets, cancellation and reliable continuation while preserving graph/SSE semantics | Runtime/route regression tests, protocol sanitization, real confirmation flow | Released: `eaabd47` |
| 4 | Tools, search and MCP | Current stable MCP protocol capabilities, accurate connection/error state, bounded discovery and execution | MCP/security/executor/search suites; real tool confirmation and history | Released: `48ba514` |
| 5 | RAG and memory | Context-enriched chunks, hybrid RRF, diversity selection and current listwise reranker integration; reliable incremental memory | Retrieval quality fixtures; ownership and memory tests; real document/query flow | Released: `4964e5c` |
| 6 | Background tasks and schedules | Fenced workers, replay barriers, deadlines, bounded queues, non-overlapping schedules and DST policy | 389 backend tests; real PostgreSQL competition/recovery; desktop/mobile task and schedule flow | Released: `336f760` |
| 7 | Authentication and API operations | Argon2id migration, expiring/revocable sessions, shared admission limits, resilient login and bounded readiness | 416 backend tests; PostgreSQL concurrency/migrations; real browser registration, logout, expiry and outage recovery | Released: `3d42d8c` |
| 8 | Frontend workspace | Claude-inspired warm neutral visual system, simpler sidebar/composer and dialogue typography; current React/Vite, resilient streaming and accessible responsive task/runtime controls | 38 frontend tests/build/audit; 417 backend tests; desktop/mobile browser chat, tools and inspector | Local acceptance passed; release pending |
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

## Iteration 3 — runtime and conversation reliability

Official references checked on 2026-10-15:

- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
  and [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts):
  persistence and side-effect replay require deliberate boundaries. This
  iteration persists approval/continuation snapshots in the existing PostgreSQL
  conversation store; it does not claim arbitrary node checkpoint/resume.
- [AnyIO cancellation](https://anyio.readthedocs.io/en/stable/cancellation.html):
  SSE disconnects need bounded, shielded asynchronous finalization.
- [pgvector Docker tags](https://github.com/pgvector/pgvector#docker): the CI
  integration database uses the published `0.8.7-pg16` image.

Implemented:

- A blocked tool saves an empty assistant message containing its trace and an
  expiring, owner-bound approval ID. It survives browser reload and process
  restart; it does not enter the model's history or trigger memory summaries.
- Confirmation locks the approval row, checks the original server-authored
  plan and commits consumption before calling `ToolExecutor`. Duplicate,
  expired, mismatched and cross-owner requests cannot execute tools. Existing
  clients without an ID must match an exact pending persisted plan.
- Cancellation is a persisted API operation. Interruption never requeues an
  approval automatically: a tool effect may already have completed. The UI
  points to continuation/tool history instead of offering replay.
- Continuation restores earlier observations and source context without
  rerunning earlier tools or repeating retrieval. Existing SSE event names and
  the `done` payload contract are preserved; approval details extend tool traces.
- Configurable 180-second run deadline, 12-second retrieval deadline,
  48,000-character model input budget and 24-hour approval lifetime. Retrieval
  timeout is visible and permits a response with available context. Oversized
  requests fail before graph side effects; cancelled streams close generators.

Acceptance:

- Full backend: 303 passed, 1 paid/live test deselected; coverage 83.16%.
  Approval store coverage 98%, context budgeting 100%. This includes real
  PostgreSQL simultaneous-confirm and confirm/cancel races.
- Ruff and 12/12 deterministic Agent evaluations: passed.
- Frontend: 21 tests and production build passed.
- Actual browser with isolated PostgreSQL and local model/MCP fixtures:
  pending approval survived refresh and backend restart; continuation produced
  one tool result and persisted answer; cancellation survived refresh.
- Mobile 390 × 844: input/actions fit; Runtime opens as a usable bottom sheet.
  Ignored screenshots: `.local/runtime-mobile-composer.jpg` and
  `.local/runtime-mobile-inspector.jpg` in the test worktree.

[GitHub quality run 38700625779](https://github.com/u2piaaa/AgentDemo/actions/runs/38700625779)
passed Python 3.11/3.12/3.14 (including PostgreSQL migrations and approval races),
frontend tests/build and both dependency audits. Commit `eaabd47` was
fast-forwarded into `run` and both release branches were pushed.

## Iteration 4 — tools, search and MCP

Official sources checked on 2026-10-15:

- [MCP versioning](https://modelcontextprotocol.io/specification/versioning)
  identifies `2026-07-28` as stable; its
  [transport](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
  carries version/capability metadata per request.
- [Python SDK](https://py.sdk.modelcontextprotocol.io/) and
  [protocol modes](https://py.sdk.modelcontextprotocol.io/protocol-versions/)
  document stable v2 and automatic legacy negotiation. The backend locks 2.3.0.
- [Fetch MCP package](https://pypi.org/project/mcp-server-fetch/2026.10.10/)
  still requires SDK 1.x. Its separate `mcp/fetch` uv project locks the service
  and dependencies independently, preserving compatibility with backend SDK 2.x.

Implemented on `codex/mcp-protocol-upgrade` from the verified module 3 baseline:

- Official SDK transports, real protocol negotiation, bounded concurrent
  paginated discovery, catalog caching and atomic registry refresh. Static
  catalogs no longer imply a successful connection. Response diagnostics redact
  configured secrets and omit remote validation payloads.
- Search-derived Fetch calls honor the executor confirmation gate. The remaining
  queue is persisted with each approval; fresh runtimes resume without replaying
  search or completed pages. Confirmed graphs can pause for another approval.
- JSON Schema 2020-12 nested input validation with local-only references,
  structured output validation/preservation, bounded nested output and binary
  omission. Cancellation records an uncertain interrupted audit outcome.
- Runtime protocol/check-time display and authenticated Refresh MCP. A failed
  direct MCP call gets a deterministic failure reply rather than an invented
  successful summary.

Local acceptance:

- 329 backend tests passed with the dedicated PostgreSQL database; coverage
  84.06%. Ruff and all 12 deterministic Agent evaluations passed.
- 21 frontend tests, production build and npm audit passed. Backend and isolated
  Fetch dependency audits found no known vulnerabilities (one transient PyPI
  TLS failure cleared on retry).
- Official SDK subprocess server negotiated `2026-07-28`; actual Fetch MCP
  2026.10.10 negotiated legacy `2025-11-25` and retrieved a public example page
  through `ToolExecutor`, after an unconfirmed call was blocked.
- Real browser: direct fetch approval survived reload; search approval survived
  backend restart; successful continuation, failed-result history and explicit
  failure answer persisted. Stopping/restarting the local MCP fixture changed
  the refreshed registry from error/zero remote tools back to ready/one tool.
- Mobile 390 × 844: composer and Runtime Tools panel fit. Desktop 1440 × 900
  checked. Screenshots remain ignored under `.local/mcp-*-mobile.jpg`.

[GitHub quality run 38706415857](https://github.com/u2piaaa/AgentDemo/actions/runs/38706415857)
passed Python 3.11/3.12/3.14, PostgreSQL integration, frontend and all dependency
audits for implementation commit `48ba514`.

The configured native GitHub MCP server was also upgraded from 1.9.0 to
[2.0.2](https://github.com/github/github-mcp-server/releases/tag/v2.0.2).
The Windows archive matched the release's SHA-256 checksum. With the existing
read-only policy, the new process negotiated `2026-07-28` and read this public
repository's README through `ToolExecutor`, returning validated structured
content. The check used the existing GitHub CLI authentication in process memory;
no credentials or downloaded binaries were added to Git.

Implementation `48ba514` and compatibility notes `f289fac` were fast-forwarded
from `test` to `run` and both branches were pushed. The stable checkout's backend
now has SDK 2.3.0; Fetch uses its own locked SDK 1.30.0 environment. `uv pip check`
passes for both. Ignored local MCP configs point to the new Fetch interpreter
and GitHub 2.0.2 binary, with prior configs backed up under `.local/backups`.
Existing service arguments and credential mappings were preserved.

Acceptance still serves the isolated database and fixtures on ports 8001/5174;
the normal 8000/5173 services remain stopped until the final combined release.

## Iteration 5 — contextual hybrid retrieval and incremental memory

Implemented extractive title/section contextualization, SQL-ranked multilingual
lexical retrieval, semantic + lexical RRF with equal-score ties, local MMR evidence
selection, and an opt-in Jina-compatible listwise adapter targeting current
`jina-reranker-v3.5`. This incorporates the user's request for newer RAG techniques.
The default pipeline adds no service dependency; neural transport is optional and
bounded, with local fallback. Details and primary references: [RAG and memory](rag-memory.md).

All retrieval paths now enforce ownership. Citation bodies remain verbatim, with
normalized positions and retrieval provenance persisted across reloads. A real
Reindex document control upgrades existing chunks while preserving their IDs and
retains existing vectors if embedding regeneration fails.

Memory compaction now persists a source cursor (including offsets within long
messages), processes older history incrementally, enforces input/output/time bounds,
and uses PostgreSQL row locks to avoid concurrent overwrites. Disabled/deleted
summaries and legacy retirement timestamps do not replay forgotten history.
Standard MCP resource text blocks also now enter runtime context correctly.

The user's added Claude-style frontend direction is recorded in iteration 8:
warm neutral backgrounds, quieter sidebar, improved dialogue typography and
composer while preserving runtime/tool/task controls.

Acceptance before release: 375 backend tests pass against PostgreSQL/pgvector
(85.27% coverage), 21 frontend tests and production build pass, Ruff passes, and
deterministic agent evaluations pass 12/12. Real browser checks cover upload,
existing-document reindex, context-aware queries, listwise HTTP contract, cited
answer persistence after reload, and 390px/1440px responsive layouts. Final source
review passed. [Implementation CI](https://github.com/u2piaaa/AgentDemo/actions/runs/38713177552)
passed on Python 3.11/3.12/3.14 with frontend and dependency audit gates.
Implementation `4964e5c` is released through `test` and `run`. The isolated acceptance database is `agent_upgrade_20261015`; HTTP model,
MCP and reranking fixtures remain under ignored `.local`. They validate application
plumbing and deterministic retrieval behavior, not neural model benchmark quality.

## Next iteration handoff

Iteration 8 upgrades the frontend with the user's Claude-style direction and
current stable React/Vite tooling. Preserve tool confirmation, runtime inspection,
RAG sources, task/schedule controls and the module 7 authentication recovery flow.
Normal serving ports 8000/5173 remain stopped until the combined release; acceptance
uses 8001/5174 and synthetic fixtures only. Never copy those settings to production.

## Iteration 6 — background tasks and scheduling

Implemented worker execution tokens with row-locked fencing across progress,
heartbeat, final state, message persistence and ToolExecutor dispatch. Retry is
bounded by attempts and a total deadline, uses jitter and stays in the database
queue. Cancellation invalidates the token; shutdown awaits cleanup and preserves
consumed attempts. A durable barrier prevents automatic replay after tool dispatch
or answer persistence. Retry messages reuse the conversation and user turn.
Agent task state is worker-owned; conflicting idempotency requests return 409.

Scheduling now coalesces to the latest eligible occurrence, enforces a catch-up
window, skips overlap, serializes manual/timed dispatch, handles DST gaps/folds,
and records decisions visible in the inspector. Invalid schedules are isolated.
Primary-source design notes and delivery limits are in
[Task execution](task-execution.md).

Local acceptance on 2026-10-15:

- 389 backend tests passed, 1 paid live test deselected; coverage 85.69%.
- 11 new real-PostgreSQL integration cases cover competing workers, stale-result
  fencing, cross-process cancellation, deadlines, shutdown, tool replay barriers,
  message deduplication, schedule competition and idempotency conflicts.
- Existing worker, route, MCP and security suites passed. One old MCP recovery
  test now allows normal SDK negotiation time after its intentional timeout.
- DST fixtures cover the New York spring gap and both fall-back folds.
- 21 frontend tests, production build, Ruff and offline evaluations (12/12) pass.
- Fresh schema upgrade, downgrade to 0008 and re-upgrade to 0009 passed in an
  isolated disposable PostgreSQL database; legacy running work is replay-blocked.
- Real browser: background success, cancellation while generating, timed automatic
  execution, pause and manual run while paused; persistence verified after reload.
  Desktop 1440x900 and mobile 390x844 inspector/composer checks passed. The synthetic
  model fixture is explicitly labelled; this does not claim paid-provider quality.
- Acceptance schedule is paused. Test backend is on 8001 and frontend on 5174;
  stable ports remain idle until combined release. Migration 0009 requires stopping
  old workers before starting upgraded processes.

[Implementation CI 38717310574](https://github.com/u2piaaa/AgentDemo/actions/runs/38717310574)
passed all Python 3.11/3.12/3.14 backend checks, PostgreSQL migrations and tests,
frontend checks and dependency audits. Implementation commit `336f760` is
released through `test` to `run` with this acceptance ledger.

## Iteration 7 — authentication and API operations

Implemented Argon2id passwords with legacy PBKDF2 rehash on successful login,
bounded hashing threads, expiring and server-revocable opaque sessions, serialized
session caps and PostgreSQL admission limits shared across workers. Registration
uses long passphrases while existing accounts retain password compatibility.
Project access gates now include registration/login. Default CORS is limited to
loopback and explicit origins, and auth responses cannot be cached.

Readiness checks the required migration within a deadline. Database connection
failures produce a generic retryable 503. Missing bearer credentials are rejected
before database acquisition. The browser preserves login during temporary outages,
waits for server-side logout, aborts old session requests and handles expiration.
Full design, configuration and migration notes are in
[Authentication and API operations](authentication.md).

Local acceptance on 2026-10-15:

- 416 backend tests passed, one paid live test deselected; coverage 86.27%.
- 26 frontend tests, production build, Ruff and 12/12 offline evaluations passed.
- Backend dependency audit reported no known vulnerabilities.
- Real PostgreSQL tests cover registration/login, legacy rehash, ownership,
  expiration, logout/all-session revocation, concurrent caps and rate limits.
- Fresh migration, downgrade to 0009, then upgrade to 0010 passed in a disposable
  database; old tokens were not renewed and password hashes remained unchanged.
- Real browser registration, legacy-account login, logout and expired-session
  recovery passed. Stopping the backend displayed Retry connection; restarting
  restored the same account, conversation and documents without another login.
- Acceptance remains isolated on ports 8001/5174 with synthetic credentials and
  labelled model fixtures. Normal stable ports remain idle until iteration 9.

[Implementation CI 38721034143](https://github.com/u2piaaa/AgentDemo/actions/runs/38721034143)
passed Python 3.11/3.12/3.14, PostgreSQL migration/integration tests, frontend
checks and dependency audits. Concurrent admission now uses PostgreSQL time
after CI exposed an elapsed-time race in an earlier application-clock version.
Implementation `3d42d8c` is released through `test` to `run` with this ledger.

## Iteration 8 — conversation workspace

Rebuilt the visual system in warm light/dark themes with a quieter sidebar,
serif answer typography, centered reading column, a compact composer and optional
Details. Runtime, citations, reindexing, tasks, schedules and MCP remain available.
Markdown answers now have safe links, code/table formatting and copy controls.
Mobile drawers contain focus and restore it on dismissal; short-screen forms
scroll within the workspace. Reduced motion, IME input and safe areas are handled.

Upgraded to React 19.3.1, Vite 8.3.4 and TypeScript 7.0.2 with a Node 24 CI runtime.
Added request cancellation, stale-stream guards, readable stop/EOF states and
reading-position preservation. The full behavior and limits are documented in
[Conversation workspace](frontend-workspace.md).

Local acceptance on 2026-10-15:

- 38 frontend tests and production build pass. npm dependency resolution is
  consistent and audit reports no known vulnerabilities.
- 417 backend tests pass, one paid live test deselected; coverage 86.27%.
  Ruff and 12/12 deterministic agent evaluations pass.
- Real browser checks include formatted answers/code copy, light/dark themes,
  restored pending approval, confirmed MCP execution and saved final answer,
  stopping generation, background completion, sources, tasks and schedules.
- Desktop 1440x900, mobile 375x812 and 320x640, and landscape 812x375 were checked.
  Drawers trap focus, Escape restores it, and short schedule forms remain reachable.
- Browser acceptance caught a completion-status error after memory transaction
  rollback. The PostgreSQL regression failed before the explicit approval refresh
  fix and passes afterwards; confirmed tools no longer report false interruption.
- Acceptance uses isolated ports 8001/5174, synthetic credentials and labelled
  model/MCP fixtures. Stable runtime startup remains part of iteration 9.
