from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, conversations, health, knowledge, tasks, tools
from app.core.config import get_settings
from app.services.plugin_registry import PluginRegistry
from app.services.task_scheduler import TaskScheduler


class AccessTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        if not settings.agent_access_token:
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)
        if not request.url.path.startswith("/api"):
            return await call_next(request)
        if request.url.path == "/api/health" or request.url.path.startswith("/api/auth"):
            return await call_next(request)
        if request.headers.get("authorization", "").startswith("Bearer "):
            return await call_next(request)
        token = request.headers.get("x-agent-access-token")
        if token != settings.agent_access_token:
            return JSONResponse({"detail": "Invalid access token"}, status_code=401)
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.plugin_registry = PluginRegistry(settings.plugin_dir)
    app.state.plugin_registry.load()
    app.state.task_scheduler = TaskScheduler()
    app.state.task_scheduler.start()
    try:
        yield
    finally:
        app.state.task_scheduler.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.add_middleware(AccessTokenMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth.router, prefix="/api")
    app.include_router(health.router, prefix="/api")
    app.include_router(conversations.router, prefix="/api")
    app.include_router(tasks.router, prefix="/api")
    app.include_router(knowledge.router, prefix="/api")
    app.include_router(tools.router, prefix="/api")
    return app


app = create_app()
