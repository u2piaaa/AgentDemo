from pathlib import Path

import pytest

from app.core.config import get_settings
from app.services.plugin_registry import PluginRegistry
from app.services.tool_executor import ToolExecutor
from app.services.web_search import get_search_provider, search_web

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def reset_web_search_settings():
    settings = get_settings()
    original = {
        "web_search_provider": settings.web_search_provider,
        "web_search_api_key": settings.web_search_api_key,
        "web_search_base_url": settings.web_search_base_url,
        "web_search_max_results": settings.web_search_max_results,
        "web_search_timeout_seconds": settings.web_search_timeout_seconds,
    }
    yield settings
    for key, value in original.items():
        setattr(settings, key, value)


def test_disabled_provider_returns_clear_error(reset_web_search_settings) -> None:
    reset_web_search_settings.web_search_provider = "disabled"

    provider = get_search_provider(reset_web_search_settings)

    assert provider.name == "disabled"
    with pytest.raises(RuntimeError, match="Web search provider is disabled"):
        provider.search("query", max_results=1)


def test_mock_provider_returns_structured_results(reset_web_search_settings) -> None:
    reset_web_search_settings.web_search_provider = "mock"
    reset_web_search_settings.web_search_max_results = 2

    payload = search_web("  latest AI news  ", settings=reset_web_search_settings)

    assert payload["query"] == "latest AI news"
    assert payload["provider"] == "mock"
    assert payload["results"][0]["url"] == "https://example.com/mock-search-result"


def test_bing_provider_requires_api_key(reset_web_search_settings) -> None:
    reset_web_search_settings.web_search_provider = "bing"
    reset_web_search_settings.web_search_api_key = ""

    with pytest.raises(RuntimeError, match="WEB_SEARCH_API_KEY is required"):
        search_web("latest AI news", settings=reset_web_search_settings)


@pytest.mark.asyncio
async def test_web_search_plugin_loads_and_runs_with_mock_provider(reset_web_search_settings) -> None:
    reset_web_search_settings.web_search_provider = "mock"
    registry = PluginRegistry(ROOT / "plugins")
    registry.load()
    tool = registry.get("web_search")

    assert tool is not None
    assert tool.manifest.permission == "network"
    assert tool.manifest.requires_confirmation is False

    result = await ToolExecutor().run(tool, {"query": "latest AI news"})

    assert result.status == "success"
    assert result.output["provider"] == "mock"
    assert result.output["results"][0]["source"] == "mock"


@pytest.mark.asyncio
async def test_web_search_plugin_rejects_empty_query(reset_web_search_settings) -> None:
    reset_web_search_settings.web_search_provider = "mock"
    registry = PluginRegistry(ROOT / "plugins")
    registry.load()
    tool = registry.get("web_search")
    assert tool is not None

    result = await ToolExecutor().run(tool, {"query": "   "})

    assert result.status == "failed"
    assert result.error == "Query is required"
