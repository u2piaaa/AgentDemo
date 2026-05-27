from fastapi import APIRouter, Request

from app.core.config import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/status")
async def auth_status() -> dict[str, bool]:
    return {"required": bool(get_settings().agent_access_token)}


@router.post("/check")
async def auth_check(request: Request) -> dict[str, bool]:
    expected = get_settings().agent_access_token
    if not expected:
        return {"ok": True}
    return {"ok": request.headers.get("x-agent-access-token") == expected}
