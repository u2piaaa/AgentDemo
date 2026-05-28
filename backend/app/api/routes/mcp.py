from fastapi import APIRouter, Request

from app.api.routes.auth import CurrentUser

router = APIRouter(prefix="/mcp", tags=["mcp"])


@router.get("/servers")
async def list_mcp_servers(request: Request, current_user: CurrentUser) -> list[dict]:
    return request.app.state.mcp_client.list_servers()


@router.get("/resources")
async def list_mcp_resources(request: Request, current_user: CurrentUser) -> list[dict]:
    return await request.app.state.mcp_client.list_resources()


@router.get("/prompts")
async def list_mcp_prompts(request: Request, current_user: CurrentUser) -> list[dict]:
    return await request.app.state.mcp_client.list_prompts()
