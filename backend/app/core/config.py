from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Personal Agent"
    environment: str = "local"
    database_url: str = "postgresql+asyncpg://agent:agent@localhost:5432/agent_demo"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    cors_origin_regex: str | None = r"https?://.*"
    agent_access_token: str = ""
    plugin_dir: Path = Path("../plugins")
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_chat_model: str = "gpt-5.4-mini"
    deepseek_api_key: str = ""
    llm_provider: str = Field(default="deepseek", min_length=1)
    llm_api_key: str = ""
    llm_api_key_required: bool = True
    llm_base_url: str = "https://api.deepseek.com"
    llm_chat_model: str = "deepseek-v4-pro"
    tool_timeout_seconds: int = 30
    max_tool_output_chars: int = 8000
    llm_request_timeout_seconds: float = Field(default=120, gt=0, le=3600)
    llm_connect_timeout_seconds: float = Field(default=10, gt=0, le=120)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_retry_base_seconds: float = Field(default=0.5, ge=0, le=30)
    llm_retry_max_seconds: float = Field(default=8, ge=0, le=60)
    llm_max_output_tokens: int = Field(default=4096, ge=1, le=131072)
    llm_output_token_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    llm_reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = None
    embedding_dimensions: int = Field(default=1536, ge=1, le=65536)
    embedding_batch_size: int = Field(default=64, ge=1, le=2048)
    rag_rerank_mode: Literal["local", "api"] = "local"
    rag_rerank_url: str = ""
    rag_rerank_model: str = "jina-reranker-v3.5"
    rag_rerank_api_key: str = Field(default="", repr=False)
    rag_rerank_timeout_seconds: float = Field(default=3, gt=0, le=30)
    rag_rerank_candidates: int = Field(default=24, ge=2, le=64)
    agent_memory_message_limit: int = 12
    agent_memory_timeout_seconds: float = Field(default=15, gt=0, le=120)
    agent_memory_input_max_chars: int = Field(default=16000, ge=1000, le=100000)
    agent_run_timeout_seconds: float = Field(default=180, gt=0, le=3600)
    agent_retrieval_timeout_seconds: float = Field(default=12, gt=0, le=120)
    agent_context_max_chars: int = Field(default=48000, ge=1000, le=1_000_000)
    agent_approval_ttl_seconds: int = Field(default=86400, ge=60, le=604800)
    agent_task_max_concurrency: int = 2
    agent_task_default_max_attempts: int = 3
    agent_task_retry_base_seconds: int = 5
    agent_task_lease_seconds: int = 180
    agent_task_heartbeat_seconds: int = 15
    task_schedule_poll_seconds: int = 5
    web_search_provider: str = "disabled"
    web_search_api_key: str = ""
    web_search_base_url: str = ""
    web_search_max_results: int = 5
    web_search_timeout_seconds: int = 10
    mcp_enabled: bool = True
    mcp_server_enabled: bool = True
    mcp_client_enabled: bool = True
    mcp_config_path: Path = Path("../mcp.servers.example.json")
    mcp_allowed_transports: list[str] = Field(
        default_factory=lambda: ["stdio", "http", "streamable-http"]
    )
    mcp_max_tool_timeout_seconds: int = 30
    mcp_remote_enabled: bool = False
    mcp_require_confirmation_by_default: bool = True
    mcp_access_policy: str = "local-only"
    mcp_server_bind_host: str = "127.0.0.1"
    mcp_max_concurrent_tool_calls: int = 4


@lru_cache
def get_settings() -> Settings:
    return Settings()
