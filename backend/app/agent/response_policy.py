import json
import re

from app.agent.constants import MCP_FETCH_TOOL_NAME, WEB_SEARCH_TOOL_NAME
from app.schemas import AgentExecutionState

RAW_TOOL_PROTOCOL_RE = re.compile(
    r"<\s*[|｜]*DSML[|｜]*tool_calls\s*>.*?(?:<\s*/\s*[|｜]*DSML[|｜]*tool_calls\s*>|$)"
    r"|<\s*tool_calls?\s*>.*?(?:<\s*/\s*tool_calls?\s*>|$)"
    r"|<\s*invoke\b[^>]*>.*?(?:<\s*/\s*invoke\s*>|$)",
    re.IGNORECASE | re.DOTALL,
)
RAW_TOOL_PROTOCOL_MARKERS = (
    "<｜｜dsml｜｜tool_calls>",
    "<||dsml||tool_calls>",
    "<tool_calls>",
    "<tool_call",
    "<invoke",
    "invoke name=",
)


class AgentResponsePolicy:
    """User-visible response safety, inventory answers, and degraded search output."""

    def _sanitize_model_answer(self, answer: str, state: AgentExecutionState) -> str:
        if not self._contains_raw_tool_protocol(answer):
            return answer
        cleaned = RAW_TOOL_PROTOCOL_RE.sub("", answer)
        cleaned = "\n".join(
            line for line in cleaned.splitlines() if not self._contains_raw_tool_protocol(line)
        ).strip()
        if cleaned:
            return cleaned + ("\n" if answer.endswith("\n") else "")
        return self._fallback_answer_after_protocol_strip(state)

    def _contains_raw_tool_protocol(self, text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in RAW_TOOL_PROTOCOL_MARKERS)

    def _fallback_answer_after_protocol_strip(self, state: AgentExecutionState) -> str:
        tool_answer = self._successful_tool_fallback_answer(state)
        if tool_answer is not None:
            return tool_answer

        message = state.message
        lowered = message.lower()
        asks_project_capability = (
            "agentdemo" in lowered or "项目" in message or "能做什么" in message
        )
        if self._message_has_cjk(message) and asks_project_capability:
            return (
                "AgentDemo 可以通过聊天界面完成问答、计划执行和知识库检索，并把运行状态实时流式展示出来。"
                "它支持通过安全的工具执行链路调用本地工具、MCP 工具、网页抓取和联网搜索。"
                "前端还会展示计划、工具历史和最终回答，方便用户确认系统每一步做了什么。"
            )
        if self._message_has_cjk(message):
            return "我拦截了模型生成的内部工具调用标记；请换一种方式重试这个请求。"
        return "I blocked internal tool-call markup from the model response. Please retry the request."

    def _successful_tool_fallback_answer(self, state: AgentExecutionState) -> str | None:
        for tool_call in reversed(state.tool_calls):
            result = tool_call.get("result")
            if not isinstance(result, dict) or result.get("status") != "success":
                continue
            tool_name = str(tool_call.get("tool_name") or "tool")
            output = result.get("output")
            if tool_name == "read_file" and isinstance(output, dict):
                content = str(output.get("content") or "").strip()
                if not content:
                    continue
                path = str(output.get("path") or tool_call.get("arguments", {}).get("path") or "file")
                asks_first_line = "第一行" in state.message or bool(
                    re.search(r"\bfirst\s+line\b", state.message, re.IGNORECASE)
                )
                if asks_first_line:
                    first_line = next(
                        (line.strip() for line in content.splitlines() if line.strip()),
                        content,
                    )
                    if self._message_has_cjk(state.message):
                        return f"文件 `{path}` 的第一行是：{first_line}"
                    return f"The first line of `{path}` is: {first_line}"
                limited = self._limit_fallback_text(content)
                if self._message_has_cjk(state.message):
                    return f"已成功读取文件 `{path}`。可用内容如下：\n\n{limited}"
                return f"The file `{path}` was read successfully. Available content:\n\n{limited}"

            text = self._tool_output_text(output)
            if not text:
                continue
            limited = self._limit_fallback_text(text)
            if self._message_has_cjk(state.message):
                return f"工具 `{tool_name}` 已成功执行。可用结果如下：\n\n{limited}"
            return f"Tool `{tool_name}` completed successfully. Available result:\n\n{limited}"
        return None

    def _tool_output_text(self, value) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return "\n".join(
                text for item in value if (text := self._tool_output_text(item))
            ).strip()
        if isinstance(value, dict):
            for key in ("text", "content"):
                text = self._tool_output_text(value.get(key))
                if text:
                    return text
            return json.dumps(value, ensure_ascii=False, default=str)
        if value is None:
            return ""
        return str(value).strip()

    def _limit_fallback_text(self, text: str, limit: int = 4000) -> str:
        return text if len(text) <= limit else f"{text[:limit]}…"

    def _tool_availability_answer(self, message: str) -> str | None:
        lowered = message.lower()
        tools = self._available_tools()
        if not tools:
            if self._requests_tool_inventory(lowered):
                return "当前没有已加载的运行时工具。"
            return None

        matching_tools = [tool for tool in tools if self._tool_matches_message(tool, lowered)]
        if matching_tools:
            if not self._requests_tool_inventory(lowered):
                return None
            tool = matching_tools[0]
            answer = f"有，我已经加载了 `{tool.manifest.name}` 工具。用途：{tool.manifest.description}"
            if tool.manifest.name == WEB_SEARCH_TOOL_NAME:
                answer += (
                    "。它可以被自动触发或直接调用，但真实联网搜索还需要在后端 `.env` "
                    "里配置 `WEB_SEARCH_PROVIDER` 和对应 API key。"
                )
            return answer

        if self._requests_tool_inventory(lowered):
            names = ", ".join(f"`{tool.manifest.name}`" for tool in tools)
            return f"当前已加载的工具有：{names}。"
        return None

    def _tool_failure_answer(self, state: AgentExecutionState) -> str | None:
        if not state.tool_calls:
            return None
        latest = state.tool_calls[-1]
        search_fallback = self._search_results_fallback_answer(state)
        if search_fallback is not None:
            return search_fallback
        if latest.get("tool_name") != WEB_SEARCH_TOOL_NAME:
            return None
        result = latest.get("result")
        if not isinstance(result, dict):
            return None
        status = str(result.get("status") or "")
        if status == "success":
            return None
        error = str(result.get("error") or status or "unknown error")
        if self._message_has_cjk(state.message):
            return (
                f"我没能完成这次联网搜索：{error}\n\n"
                "因为没有成功获取搜索结果，我不会编造今天的新闻摘要。"
                "请在后端配置 `WEB_SEARCH_PROVIDER` 和对应 `WEB_SEARCH_API_KEY` 后重试。"
            )
        return (
            f"I could not complete the web search: {error}\n\n"
            "Because no search results were retrieved, I will not invent a news summary. "
            "Configure `WEB_SEARCH_PROVIDER` and the matching `WEB_SEARCH_API_KEY` on the "
            "backend, then try again."
        )

    def _message_has_cjk(self, message: str) -> bool:
        return any("\u4e00" <= char <= "\u9fff" for char in message)

    def _search_results_fallback_answer(self, state: AgentExecutionState) -> str | None:
        search_call = self._latest_successful_web_search_call(state)
        if search_call is None:
            return None
        enrichment_calls = [
            item
            for item in state.tool_calls
            if item.get("tool_name") == MCP_FETCH_TOOL_NAME and item.get("search_enrichment")
        ]
        if not enrichment_calls:
            return None
        if any(
            isinstance(item.get("result"), dict)
            and item["result"].get("status") == "success"
            for item in enrichment_calls
        ):
            return None
        result = search_call.get("result")
        if not isinstance(result, dict):
            return None
        output = result.get("output")
        results = self._search_result_items(output)[:5]
        if not results:
            return None
        provider = "web_search"
        if isinstance(output, dict):
            provider = str(output.get("provider") or provider)
        lines: list[str] = []
        for index, item in enumerate(results, start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "Untitled").strip()
            snippet = str(item.get("snippet") or "").strip()
            url = str(item.get("url") or "").strip()
            if self._message_has_cjk(state.message):
                line = f"{index}. {title}"
                if snippet:
                    line += f"\n   摘要：{snippet}"
                if url:
                    line += f"\n   来源：{url}"
            else:
                line = f"{index}. {title}"
                if snippet:
                    line += f"\n   Summary: {snippet}"
                if url:
                    line += f"\n   Source: {url}"
            lines.append(line)
        if not lines:
            return None
        if self._message_has_cjk(state.message):
            return (
                f"我已经完成 {provider} 搜索，但搜索结果网页正文全部抓取失败，"
                "所以先基于搜索结果标题、摘要和来源链接给你一个降级版总结：\n\n"
                + "\n\n".join(lines)
                + "\n\n注意：以上不是网页正文深度分析；请稍后重试网页抓取或更换可访问来源。"
            )
        return (
            f"The {provider} search succeeded, but every search-result page fetch failed. "
            "Here is a fallback summary from the search titles, snippets, and source URLs:\n\n"
            + "\n\n".join(lines)
            + "\n\nNote: this is not a full page-body analysis; retry fetching later or use accessible sources."
        )

    def _latest_successful_web_search_call(self, state: AgentExecutionState) -> dict | None:
        for item in reversed(state.tool_calls):
            if item.get("tool_name") != WEB_SEARCH_TOOL_NAME:
                continue
            result = item.get("result")
            if isinstance(result, dict) and result.get("status") == "success":
                return item
        return None

    def _requests_tool_inventory(self, lowered_message: str) -> bool:
        english_patterns = (
            r"\b(?:what|which)\s+(?:runtime\s+)?tools?\b",
            r"\b(?:list|show|check)\s+(?:the\s+)?"
            r"(?:(?:available|installed|loaded|runtime)\s+)?tools?\b",
            r"\b(?:available|installed|loaded)\s+(?:runtime\s+)?tools?\b",
            r"\b(?:do|does)\s+(?:you|agentdemo|the\s+runtime|this\s+agent)"
            r".{0,40}\b(?:have|support)\b.{0,40}\btools?\b",
            r"\bdo\s+you\s+(?:have|support)\s+[\w.:-]+\b",
            r"\b(?:is|are)\s+[\w.:-]+\s+(?:tool\s+)?"
            r"(?:available|installed|loaded)\b",
        )
        if any(re.search(pattern, lowered_message) for pattern in english_patterns):
            return True
        if "工具" not in lowered_message:
            return False
        chinese_patterns = (
            r"(?:有哪些|有什么|哪些|列出|列一下|展示|显示|查看|检查).{0,30}工具",
            r"工具.{0,12}(?:有哪些|有什么|哪些|列表|清单|是否可用|能否使用)",
            r"(?:有没有|有无|是否有|是否支持|支持哪些).{0,30}工具",
            r"(?:你|系统|项目|agentdemo).{0,12}(?:有|支持).{0,30}工具(?:吗|么|？|\?)?",
        )
        return any(re.search(pattern, lowered_message) for pattern in chinese_patterns)

    def _tool_matches_message(self, tool, lowered_message: str) -> bool:
        identifiers = {
            tool.manifest.name.lower(),
            str(tool.provider_tool_id or "").lower(),
            str(tool.server_name or "").lower(),
        }
        if tool.server_name:
            identifiers.add(f"{tool.server_name.lower()} mcp")
            identifiers.add(f"mcp {tool.server_name.lower()}")
        return any(identifier and identifier in lowered_message for identifier in identifiers)
