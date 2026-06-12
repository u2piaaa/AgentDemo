# Project Workflow

- Use Git to manage this project.
- Use `test` as the development branch for any requested code changes.
- Use `run` as the running/stable branch.
- For future change requests, implement work on `test` first, run the relevant checks, then merge the tested result into `run`.
- Push the resulting branches to the user's GitHub repository.
- The GitHub repository for this project should be private.
- Do not commit local runtime data, downloaded binaries, secrets, virtual environments, dependency folders, logs, or build outputs.

# Worktree And Release Rules

- Confirm `git worktree list`, the current branch, and `git status --short --branch` before changing files. This repo commonly has separate worktrees for `test`, `run`, integration, and feature branches.
- Normal fixes should be made in the `test` worktree first, verified there, committed, merged into `run`, and pushed for both branches.
- For large refactors or handoff prompts, create or use an isolated feature branch from the latest verified baseline and do not merge into `run` until the user asks or acceptance checks pass.
- Branch state and live serving state can diverge. When the user says a bug still exists, verify which worktree and process are actually serving ports `8000` and `5173` before assuming the code change failed.

# Architecture Notes

- AgentDemo is a local-first personal AI agent app, not just a chat UI. It combines FastAPI, React/Vite, PostgreSQL/pgvector, conversation persistence, RAG, tool execution, MCP integration, task tracking, and memory summaries.
- The main chat API is `POST /api/conversations/chat/stream`; confirmed tool continuation is `POST /api/conversations/chat/confirm/stream`.
- Runtime orchestration is LangGraph-based. Preserve the graph flow and the public SSE event contract: `status`, `plan`, `tool_call`, `tool_result`, `token`, `done`, and `error`.
- Keep `ToolExecutor` as the safety and audit boundary for tools, including schema validation, confirmation gating, timeout handling, output limiting, MCP policy, and `ToolCall` persistence.
- Do not bypass `ToolExecutor` for MCP, Fetch MCP, GitHub MCP, or search-derived fetch calls.

# Search, Fetch, And MCP

- Keep direct URL fetch, GitHub URL handling, Hugging Face URL handling, and generic `web_search` as distinct planning routes.
- Generic webpage fetch and summarization should use Fetch MCP, normally exposed as `mcp.fetch.fetch`.
- GitHub repository/code links should prefer GitHub MCP repository-aware tools instead of generic fetch when those tools are configured.
- `web_search` results may be enriched by fetching selected result URLs, but the follow-up fetch must still go through `ToolExecutor` with confirmation, policy, timeout, and audit behavior intact.
- Normalize MCP error payloads such as `isError` into failed tool results so UI state, audit records, and model context agree.
- Freshness/news queries such as "today AI news", "latest", "news", "sources", or Chinese equivalents should trigger `web_search`; persona/style prompts and ordinary project-introduction prompts should not trigger search just because they contain a freshness-like word.

# Runtime And UX Pitfalls

- Raw model tool protocol text such as DSML `tool_calls`, `invoke`, or similar tool-call markup must never appear in user-visible assistant tokens, final answers, or saved assistant messages.
- Direct URL summarization should surface confirmation quickly instead of spending a long time in `retrieving_context` before showing `Confirm`.
- Waiting states should tell the user the system is still working, especially around `retrieving_context`, search, fetch, and confirmed-tool continuation.
- The frontend must keep pending tool confirmation UI stable across streaming state changes and conversation refreshes.
- Mobile checks should include the chat input and Runtime inspector so dense tool/runtime panels do not overlap or become unusable.

# Verification Expectations

- For backend/runtime changes, run focused pytest suites around `tests/test_agent_runtime.py`, `tests/test_web_search_plugin.py`, `tests/test_mcp_tooling.py`, `tests/test_mcp_security.py`, `tests/test_tool_executor.py`, or route tests as relevant.
- For frontend changes, run the existing frontend build or test command before merging.
- For user-visible behavior, automated tests are not enough. Use the real browser UI when feasible and verify the actual chat flow, tool history, confirmation UI, and final answer.
- Auth health requires more than `/api/auth/status`; if login or register fails, verify PostgreSQL, then test real register/login write paths.
- Local runtime services usually use backend `8000`, frontend `5173`, and local PostgreSQL under `.local/postgres`. Keep logs, local config, tokens, tunnels, screenshots, and generated runtime files out of commits.
