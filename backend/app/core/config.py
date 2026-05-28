from functools import lru_cache
from pathlib import Path

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
    llm_base_url: str = "https://api.deepseek.com"
    llm_chat_model: str = "deepseek-v4-pro"
    tool_timeout_seconds: int = 30
    max_tool_output_chars: int = 8000
    llm_request_timeout_seconds: int = 120
    embedding_dimensions: int = 1536
    agent_memory_message_limit: int = 12
    mcp_enabled: bool = True
    mcp_server_enabled: bool = True
    mcp_client_enabled: bool = True
    mcp_config_path: Path = Path("../mcp.servers.example.json")
    mcp_allowed_transports: list[str] = Field(default_factory=lambda: ["stdio"])
    mcp_max_tool_timeout_seconds: int = 30
    mcp_remote_enabled: bool = False
    mcp_require_confirmation_by_default: bool = True
    mcp_access_policy: str = "local-only"
    mcp_server_bind_host: str = "127.0.0.1"
    mcp_max_concurrent_tool_calls: int = 4


@lru_cache
def get_settings() -> Settings:
    return Settings()
