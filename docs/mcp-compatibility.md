# MCP Compatibility

AgentDemo's MCP layer adapts MCP concepts into the existing runtime, registry,
executor, audit, task, RAG, and frontend surfaces.

## Server Mode

`backend/app/mcp/server.py` exposes local plugin tools with MCP-compatible tool
schemas. Calls are delegated to `ToolExecutor`, preserving validation,
confirmation, permission metadata, audit rows, trace IDs, and task binding.

The default server policy is local-only. Remote exposure must be explicitly
enabled with `mcp_remote_enabled=true`.

## Client Mode

`backend/app/mcp/client.py` loads configured MCP servers through
`backend/app/mcp/config.py`. The implementation supports configured
stdio/mock-style servers for local development and tests, plus remote HTTP,
SSE, and Streamable HTTP MCP servers for hosted providers such as Hugging Face.
HTTP transports run the same MCP JSON-RPC flow used by stdio:
`initialize`, `notifications/initialized`, `tools/list`, and `tools/call`.

For Hugging Face, open the logged-in settings page at
https://huggingface.co/settings/mcp and copy the generated client snippet. Map
the generated server URL to this project's `url` field, the transport to
`transport`, and any generated headers to `headers`. Keep tokens in `.env` or
the process environment, for example:

```json
{
  "servers": {
    "huggingface": {
      "enabled": false,
      "transport": "http",
      "url": "https://huggingface.co/mcp",
      "headers": {
        "Authorization": "Bearer ${HUGGINGFACE_TOKEN}"
      }
    }
  }
}
```

## Tool Mapping

Local plugin fields map to MCP as:

- `name` to `name`
- `description` to `description`
- `parameters` to `inputSchema`
- `permission` to `annotations.permission`
- `requires_confirmation` to `annotations.requires_confirmation`

External MCP tools map into `RegisteredTool` with `provider=mcp_server`,
`provider_tool_id`, `server_name`, and `transport` metadata. Local Python
plugins remain `provider=local_plugin`.

## Resource And Prompt Mapping

MCP resources can be loaded into runtime context when explicitly referenced.
They can also be imported into knowledge with:

- `source_type=mcp_resource`
- `source_uri=<MCP resource URI>`
- `user_id=<current user>`

Citations include the MCP `source_uri`. MCP prompts are listed by server and can
be added to runtime context by name.

## Security Model

Access policies are `local-only`, `authenticated`, `disabled`, and `admin-only`.
Permission classes are `read`, `write`, `execute`, `network`, and `destructive`.
Risky permissions require confirmation. Config files must not contain plaintext
secrets; use environment placeholders such as `${API_KEY}`.
Authorization headers may use bearer placeholders such as
`Bearer ${HUGGINGFACE_TOKEN}`. Hugging Face MCP tools are registered as network
tools by default, so calls require confirmation unless a future explicit policy
marks a public read-only search tool as safe to auto-run.

## Audit, Trace, And Tasks

MCP tool calls return the standard `ToolRunResponse` and write provider metadata
to tool-call audit records. When a call is bound to a task, task metadata records
`mcp_tool_call_started`, `mcp_tool_call_finished`, or `mcp_tool_call_failed`.

The frontend renders provider, server name, MCP resources, MCP prompts, and MCP
task events in the runtime inspector and execution timeline.
