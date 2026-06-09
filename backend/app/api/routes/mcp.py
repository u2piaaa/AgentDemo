from fastapi import APIRouter, Request

from app.api.routes.auth import CurrentUser

router = APIRouter(prefix="/mcp", tags=["mcp"])


@router.get("/servers")
async def list_mcp_servers(request: Request, current_user: CurrentUser) -> list[dict]:
    servers = request.app.state.mcp_client.list_servers()
    tool_registry = getattr(request.app.state, "tool_registry", None)
    if tool_registry is None:
        return servers

    errors = tool_registry.list_mcp_errors()
    registered_counts = {server["name"]: 0 for server in servers}
    for tool in tool_registry.list_tools():
        server_name = getattr(tool, "server_name", None)
        if server_name in registered_counts:
            registered_counts[server_name] += 1

    enriched = []
    for server in servers:
        server_name = str(server["name"])
        item = {**server, "registered_tool_count": registered_counts.get(server_name, 0)}
        if server_name in errors:
            item["status"] = "error"
            item["error"] = errors[server_name]
        elif item["registered_tool_count"] > 0:
            item["status"] = "connected"
        enriched.append(item)
    return enriched


@router.get("/resources")
async def list_mcp_resources(request: Request, current_user: CurrentUser) -> list[dict]:
    return await request.app.state.mcp_client.list_resources()


@router.get("/prompts")
async def list_mcp_prompts(request: Request, current_user: CurrentUser) -> list[dict]:
    return await request.app.state.mcp_client.list_prompts()
