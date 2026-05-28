from fastapi import APIRouter, HTTPException, Request

from app.api.routes.auth import CurrentUser
from app.schemas import ToolManifestRead, ToolRunRequest, ToolRunResponse
from app.services.tool_executor import ToolExecutor

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("", response_model=list[ToolManifestRead])
async def list_tools(request: Request, current_user: CurrentUser) -> list[ToolManifestRead]:
    registry = request.app.state.plugin_registry
    return [tool.to_read_model() for tool in registry.list_tools()]


@router.post("/{tool_name}/run", response_model=ToolRunResponse)
async def run_tool(
    tool_name: str,
    payload: ToolRunRequest,
    request: Request,
    current_user: CurrentUser,
) -> ToolRunResponse:
    registry = request.app.state.plugin_registry
    tool = registry.get(tool_name)
    if tool is None or not tool.manifest.enabled:
        raise HTTPException(status_code=404, detail="Tool not found")
    return await ToolExecutor().run(tool, payload.arguments)
