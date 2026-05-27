# Agent Task Board

This board tracks the executable-agent upgrade across short-lived agent branches.

| id | title | owner_agent | branch | status | depends_on | touched_files | commits | tests_run | risks | handoff_notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SEC-1 | Authentication and access boundaries | Security Agent | agent/security | verified_on_test | none | backend/app/main.py; backend/app/api/routes/auth.py; backend/app/api/routes/tools.py; backend/app/api/routes/tasks.py; backend/app/models/task.py; backend/tests/* | d26fbfc; 3a23feb; 238b557 | `backend/.venv/Scripts/python.exe -m pytest tests/test_access_token_middleware.py tests/test_tool_routes.py tests/test_task_routes.py` = 19 passed | shared tasks model/routes with Task Agent | Added `Task.user_id`; Task Agent must add migration. |
| TOOL-1 | Safe auditable tool layer | Tooling Agent | agent/tooling | verified_on_test | SEC-1 for authenticated route context | backend/app/services/plugin_registry.py; backend/app/services/tool_executor.py; backend/app/models/tool.py; plugins/read_file/*; backend/app/schemas.py; backend/tests/* | c8bed0b; b0f4cd5; 399ec28; 878736d; 2182bb5 | `backend/.venv/Scripts/python.exe -m pytest tests/test_plugin_registry.py tests/test_tool_executor.py tests/test_read_file_plugin.py` = 17 passed | shared schemas with Runtime Agent | Added `ToolCall.user_id` and `input_summary`; Task Agent must add migration. |
| RUN-1 | Executable agent runtime loop | Runtime Agent | agent/runtime | todo | TOOL-1 | backend/app/agent/runtime.py; backend/app/services/model_gateway.py; backend/app/schemas.py; backend/tests/* | pending | pending | shared runtime/schemas with RAG Agent | Runtime owns runtime.py during tool planning and SSE protocol work. |
| TASK-1 | Durable task lifecycle | Task Agent | agent/tasks | in_progress | SEC-1 | backend/app/api/routes/tasks.py; backend/app/services/task_scheduler.py; backend/app/models/task.py; backend/migrations/*; backend/tests/* | pending | pending | migration ordering; shared task route/model | Must include migrations for `tasks.user_id`, `tasks.trace_id` if added, `tool_calls.user_id`, and `tool_calls.input_summary`. |
| FE-1 | Agent execution UI | Frontend Agent | agent/frontend | todo | RUN-1; TASK-1 | frontend/src/App.tsx; frontend/src/api.ts; frontend/src/types.ts; frontend/src/styles.css | pending | pending | UI depends on final SSE and task API shape | Frontend starts after runtime stream events and task panel APIs settle. |
| RAG-1 | RAG citations and memory | RAG / Memory Agent | agent/rag-memory | todo | SEC-1 for user isolation; coordinate with RUN-1 for runtime loading | backend/app/services/rag.py; backend/app/models/knowledge.py; backend/app/models/conversation.py; backend/app/api/routes/knowledge.py; backend/app/api/routes/memory.py; backend/tests/* | pending | pending | runtime.py conflict if memory loading is added too early | RAG may implement services/routes first and leave runtime hook as handoff until Runtime Agent finishes. |
| DOC-1 | Documentation and QA closure | Docs / QA Agent | agent/docs-qa | todo | SEC-1; TOOL-1; RUN-1; TASK-1; FE-1; RAG-1 | README.md; docs/*; tests as needed | pending | pending | docs can drift until final APIs settle | Docs / QA keeps notes early but finalizes after feature branches merge to test. |

## Integration Log

- 2026-05-28: Initialized `merge` and `agent/*` branches from `test` at `291529c`.
- 2026-05-28: Merged `agent/security` into `test` at `54dcca1`; verified security route tests.
- 2026-05-28: Merged `agent/tooling` into `test` at `8d22d62`; verified tooling tests.

## Verification Log

- Baseline frontend: `npm run build` passed.
- Baseline backend with system Python failed because `pytest` was not installed; project venv pytest is available at `backend/.venv/Scripts/python.exe`.
- Security route tests: 19 passed.
- Tooling tests: 17 passed.
