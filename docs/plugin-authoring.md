# Plugin Authoring

Local plugins are Python functions described by a JSON manifest. The backend
loads enabled manifests from `plugins/*/manifest.json` during startup and exposes
them through `/api/tools`.

## Directory Shape

```text
plugins/
  read_file/
    manifest.json
    tool.py
```

`entrypoint` is resolved relative to the plugin directory, so `tool.py:run`
means "load `plugins/read_file/tool.py` and call `run(...)`".

## Manifest Fields

| Field | Required | Description |
| --- | --- | --- |
| `name` | yes | Stable tool name. Must match `^[a-zA-Z0-9_.-]+$`. |
| `description` | yes | Short user-facing and model-facing description. |
| `permission` | no | Permission label displayed in the UI and audit data. Defaults to `safe`. |
| `requires_confirmation` | no | Blocks execution unless the caller passes confirmation. Defaults to `false`. |
| `parameters` | no | JSON Schema-like object used for argument validation. Defaults to an object schema. |
| `timeout_seconds` | no | Tool timeout from 1 to 300 seconds. The executor also applies the global timeout cap. |
| `output_strategy` | no | Output limiting policy. `max_chars` is currently honored by the executor. |
| `entrypoint` | yes | Python module path and function name, for example `tool.py:run`. |
| `enabled` | no | Disabled tools are hidden from execution. Defaults to `true`. |

Example:

```json
{
  "name": "read_file",
  "description": "Read a UTF-8 text file from an allowed local path.",
  "permission": "filesystem_read",
  "requires_confirmation": false,
  "timeout_seconds": 10,
  "enabled": true,
  "entrypoint": "tool.py:run",
  "parameters": {
    "type": "object",
    "required": ["path"],
    "additionalProperties": false,
    "properties": {
      "path": {
        "type": "string",
        "format": "path",
        "description": "Absolute or workspace-relative path to a text file."
      }
    }
  },
  "output_strategy": {
    "mode": "truncate",
    "max_chars": 8000,
    "summary_max_chars": 500
  }
}
```

## Parameter Schema

The executor validates a practical JSON Schema subset before the handler runs:

- Top-level `parameters.type` must be `object`.
- `required` fields must be present.
- `additionalProperties: false` rejects unknown arguments.
- Property `type` supports `string`, `integer`, `number`, `boolean`, `object`,
  `array`, `null`, or a list of those types.
- Property `format` supports `path` and `uuid`.

Unsupported JSON Schema keywords are allowed in the manifest for documentation,
but they are not enforced by the current executor. If a tool needs stricter
validation, add explicit checks inside the handler and cover them with tests.

## Permissions

`permission` is currently a label, not a complete sandbox policy. Use consistent,
specific labels so reviewers and the UI can identify the risk class:

- `safe` for deterministic, read-only, low-risk helpers.
- `filesystem_read` for reading workspace files.
- `filesystem_write` for tools that write files.
- `network` for tools that call remote services.
- `shell` for tools that run local commands.

Tools with write, network, shell, account, or destructive behavior should set
`requires_confirmation: true`.

## Confirmation

The executor supports confirmation through `ToolRunRequest.confirmed` and the
internal `confirmed` flag. If `requires_confirmation` is true and the caller does
not confirm, the executor returns a failed response with:

```text
Tool requires confirmation before execution
```

That blocked result is still audited. In chat, the frontend can continue a
blocked tool call by sending the original message, tool name, and arguments to
`POST /api/conversations/chat/confirm/stream`. The continuation stream emits the
usual `plan`, `tool_call`, `tool_result`, `token`, and `done` events and stores a
new assistant message with the confirmed tool result.

## Safe Paths

The sample `read_file` tool demonstrates the expected path posture:

- Resolve relative paths under the workspace root.
- Reject paths outside the workspace after resolution.
- Reject protected path segments such as `.env`, `.local`, `.venv`,
  `downloads`, `node_modules`, `dist`, and build/cache directories.
- Read text as UTF-8.
- Return bounded content rather than raw unbounded file data.

New filesystem tools should follow the same pattern and add extra deny rules for
secrets, generated binaries, dependency folders, logs, and build outputs.

## Output Summary Strategy

The executor applies two output controls:

- `_limit_output()` truncates string output and string values inside dictionary
  output using `output_strategy.max_chars`, capped by `MAX_TOOL_OUTPUT_CHARS`.
- `_summarize()` serializes the output and stores at most 500 characters, also
  capped by `MAX_TOOL_OUTPUT_CHARS`.

Handlers should return structured dictionaries when possible, with a small
summary-friendly field for model context and audit review. Avoid returning raw
large blobs, secrets, downloaded binaries, or build artifacts.

## Handler Guidelines

- Keep the public handler signature aligned with the manifest properties.
- Raise clear `ValueError`, `FileNotFoundError`, or `PermissionError` messages
  for user-fixable issues.
- Keep side effects explicit in the tool name and permission label.
- Make operations idempotent when practical.
- Do not read `.env`, token files, virtual environments, dependency folders,
  logs, downloads, or build outputs.
- Use the manifest timeout as a realistic upper bound. Long-running work should
  be represented as a task instead of a blocking plugin call.

## Plugin Testing

Recommended coverage for each plugin:

- Registry test: the manifest loads and exposes the expected read model.
- Argument validation test: missing, wrong-type, and unknown arguments fail.
- Handler test: valid inputs return the expected structured output.
- Safety test: forbidden paths or forbidden operations are rejected.
- Executor test: timeout, confirmation, output truncation, and audit summary
  behavior are covered when relevant.

Existing examples:

```powershell
cd D:\workplace\AgentDemo\backend
.\.venv\Scripts\python.exe -m pytest tests/test_plugin_registry.py tests/test_tool_executor.py tests/test_read_file_plugin.py
```
