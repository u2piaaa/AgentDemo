# Agent Task Board

This board tracks the executable-agent upgrade across short-lived agent branches.

| id | title | owner_agent | branch | status | depends_on | touched_files | commits | tests_run | risks | handoff_notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SEC-1 | Authentication and access boundaries | Security Agent | agent/security | verified_on_test | none | backend/app/main.py; backend/app/api/routes/auth.py; backend/app/api/routes/tools.py; backend/app/api/routes/tasks.py; backend/app/models/task.py; backend/tests/* | d26fbfc; 3a23feb; 238b557 | `backend/.venv/Scripts/python.exe -m pytest tests/test_access_token_middleware.py tests/test_tool_routes.py tests/test_task_routes.py` = 19 passed | shared tasks model/routes with Task Agent | Added `Task.user_id`; Task Agent must add migration. |
| TOOL-1 | Safe auditable tool layer | Tooling Agent | agent/tooling | verified_on_test | SEC-1 for authenticated route context | backend/app/services/plugin_registry.py; backend/app/services/tool_executor.py; backend/app/models/tool.py; plugins/read_file/*; backend/app/schemas.py; backend/tests/* | c8bed0b; b0f4cd5; 399ec28; 878736d; 2182bb5 | `backend/.venv/Scripts/python.exe -m pytest tests/test_plugin_registry.py tests/test_tool_executor.py tests/test_read_file_plugin.py` = 17 passed | shared schemas with Runtime Agent | Added `ToolCall.user_id` and `input_summary`; Task Agent must add migration. |
| RUN-1 | Executable agent runtime loop | Runtime Agent | agent/runtime | verified_on_test | TOOL-1 | backend/app/agent/runtime.py; backend/app/services/model_gateway.py; backend/app/schemas.py; backend/tests/* | 3b4f16f; d0972cf; 9f8c727; f6f00b9; 26518ea; 0d15937; d450c57; 8e6e272; 37dc6fa; b705adf; 02fa2c0 | `backend/.venv/Scripts/python.exe -m pytest tests/test_agent_runtime.py tests/test_agent_runtime_live.py tests/test_model_gateway.py tests/test_tool_executor.py tests/test_read_file_plugin.py` = 31 passed, 1 skipped | shared runtime/schemas with RAG Agent | Runtime merged before RAG runtime memory hook; memory generation remains a documented follow-up unless Docs/QA closes it. |
| TASK-1 | Durable task lifecycle | Task Agent | agent/tasks | verified_on_test | SEC-1 | backend/app/api/routes/tasks.py; backend/app/services/task_scheduler.py; backend/app/models/task.py; backend/migrations/*; backend/tests/* | 269d9b8; 889d912; 5cabd69; 95007b0; 4c6c4b5; 61d9d19 | `backend/.venv/Scripts/python.exe -m pytest tests/test_task_routes.py tests/test_task_scheduler.py` = 23 passed | migration ordering; shared task route/model | Task Agent prematurely pushed `run`; `origin/run` was restored to stable `291529c` and must not move again until final merge verification. |
| FE-1 | Agent execution UI | Frontend Agent | agent/frontend | verified_on_test | RUN-1; TASK-1 | frontend/src/App.tsx; frontend/src/api.ts; frontend/src/types.ts; frontend/src/styles.css | a8910b3 | `npm ci`; `npm run build` = passed | UI depends on final SSE and task API shape | Backend lacks a continue-after-confirmation endpoint, so confirm UI is shown disabled with cancel handled locally. |
| RAG-1 | RAG citations and memory | RAG / Memory Agent | agent/rag-memory | verified_on_test | SEC-1 for user isolation; coordinate with RUN-1 for runtime loading | backend/app/services/rag.py; backend/app/models/knowledge.py; backend/app/models/conversation.py; backend/app/api/routes/knowledge.py; backend/app/api/routes/memory.py; backend/tests/* | 2621ed0; 61c9861; 1330f1e | `backend/.venv/Scripts/python.exe -m pytest tests/test_rag.py tests/test_memory_routes.py` = 8 passed, 3 warnings | runtime.py conflict if memory loading is added too early | RAG commits were cherry-picked because branch had an old base; `Register memory routes` added API mounting. |
| DOC-1 | Documentation and QA closure | Docs / QA Agent | agent/docs-qa | verified_on_branch | SEC-1; TOOL-1; RUN-1; TASK-1; FE-1; RAG-1 | README.md; docs/* | 19d11f0; 035b35c; ed78b36; final checklist commit | `git diff --check` = passed | docs must be rechecked if final APIs change | Documented executable runtime, plugin authoring, testing strategy, final regression checklist, and known release limits. |

## Integration Log

- 2026-05-28: Initialized `merge` and `agent/*` branches from `test` at `291529c`.
- 2026-05-28: Merged `agent/security` into `test` at `54dcca1`; verified security route tests.
- 2026-05-28: Merged `agent/tooling` into `test` at `8d22d62`; verified tooling tests.
- 2026-05-28: Task Agent merged and pushed `agent/tasks` into `test` and prematurely into `run` at `61d9d19`; `origin/run` was restored to `291529c` per release rule.
- 2026-05-28: Merged `agent/runtime` into local `test` at `7fda12b`; verified runtime and task tests.
- 2026-05-28: Merged `agent/frontend` into local `test`; verified frontend build.
- 2026-05-28: Cherry-picked RAG / Memory commits `2621ed0` and `61c9861`, then committed `1330f1e Register memory routes`.
- 2026-05-28: Docs / QA branched `agent/docs-qa` from integrated `test` at `3ffb795` and updated README plus docs handoff material.

## Verification Log

- Baseline frontend: `npm run build` passed.
- Baseline backend with system Python failed because `pytest` was not installed; project venv pytest is available at `backend/.venv/Scripts/python.exe`.
- Security route tests: 19 passed.
- Tooling tests: 17 passed.
- Runtime/tool tests: 31 passed, 1 skipped.
- Task tests: 23 passed.
- Frontend build after `npm ci`: passed; npm audit reports 2 moderate vulnerabilities in existing dependencies.
- RAG / memory tests: 8 passed, 3 `datetime.utcnow()` deprecation warnings.
- Docs / QA: `git diff --check` passed for documentation changes.
