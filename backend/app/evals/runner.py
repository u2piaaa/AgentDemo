from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.agent.runtime import AgentRuntime
from app.schemas import AgentExecutionState
from app.services.model_gateway import ModelRoute, StructuredToolPlan
from app.services.plugin_registry import PluginManifest, RegisteredTool
from app.services.rag import RagService


DEFAULT_SUITE_PATH = Path(__file__).with_name("scenarios.json")


class EvalGateway:
    """Offline-only gateway used to exercise deterministic planning routes."""

    def route(self, task_type: str, prompt: str) -> ModelRoute:
        return ModelRoute(model_name="offline-eval", provider="offline", reason=task_type)

    def plan_tool_call(
        self,
        prompt: str,
        candidates: list[dict[str, Any]],
    ) -> StructuredToolPlan:
        lowered = prompt.casefold()
        for tool in candidates:
            names = (tool.get("name"), tool.get("provider_tool_id"))
            if any(str(name or "").casefold() in lowered for name in names if name):
                return StructuredToolPlan(
                    no_tool=False,
                    tool_name=str(tool["name"]),
                    arguments={},
                    reason="The scenario explicitly named an available tool.",
                )
        return StructuredToolPlan(no_tool=True, reason="No MCP tool matched the scenario.")

    async def normalize_web_search_query(self, message: str) -> str:
        return ""


class EvalRegistry:
    def __init__(self, tools: list[RegisteredTool]) -> None:
        self.tools = {tool.manifest.name: tool for tool in tools}
        self.mcp_client = None

    def get(self, name: str) -> RegisteredTool | None:
        return self.tools.get(name)

    def list_tools(self) -> list[RegisteredTool]:
        return list(self.tools.values())


class NullRag:
    async def search(self, query: str, conversation_id=None) -> list:
        return []


def load_suite(path: Path | str | None = None) -> dict[str, Any]:
    suite_path = Path(path) if path is not None else DEFAULT_SUITE_PATH
    return json.loads(suite_path.read_text(encoding="utf-8"))


async def evaluate_suite(suite: dict[str, Any]) -> dict[str, Any]:
    results = []
    for scenario in suite.get("scenarios", []):
        results.append(await evaluate_scenario(scenario))

    passed = sum(1 for result in results if result["passed"])
    total = len(results)
    score = passed / total if total else 0.0
    category_totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for result in results:
        category = result["category"]
        category_totals[category][1] += 1
        if result["passed"]:
            category_totals[category][0] += 1
    categories = {
        category: {
            "passed": counts[0],
            "total": counts[1],
            "score": counts[0] / counts[1],
        }
        for category, counts in sorted(category_totals.items())
    }
    return {
        "name": suite.get("name", "agent-evals"),
        "minimum_score": float(suite.get("minimum_score", 1.0)),
        "passed": passed,
        "total": total,
        "score": score,
        "categories": categories,
        "results": results,
    }


async def evaluate_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    kind = str(scenario.get("kind") or "")
    category = str(scenario.get("category") or kind)
    try:
        if kind == "plan":
            actual = await _evaluate_plan(scenario)
        elif kind == "answer_safety":
            actual = _evaluate_answer_safety(scenario)
        elif kind == "grounding_context":
            actual = _evaluate_grounding_context(scenario)
        elif kind == "keyword_terms":
            actual = RagService(session=None)._keyword_terms(str(scenario.get("query") or ""))  # type: ignore[arg-type]
        else:
            raise ValueError(f"Unsupported scenario kind: {kind}")
        failures = _expectation_failures(actual, scenario.get("expected", {}))
    except Exception as exc:
        actual = None
        failures = [f"raised {type(exc).__name__}: {exc}"]
    return {
        "id": str(scenario.get("id") or "unnamed"),
        "category": category,
        "passed": not failures,
        "failures": failures,
        "actual": actual,
    }


async def _evaluate_plan(scenario: dict[str, Any]) -> dict[str, Any]:
    tools = [_tool_from_spec(spec) for spec in scenario.get("tools", [])]
    runtime = _runtime(tools)
    state = AgentExecutionState(message=str(scenario.get("message") or ""))
    return (await runtime._plan_next_step(state)).model_dump()


def _evaluate_answer_safety(scenario: dict[str, Any]) -> str:
    runtime = _runtime([])
    state = AgentExecutionState(
        message=str(scenario.get("message") or ""),
        tool_calls=scenario.get("tool_calls", []),
    )
    return runtime._sanitize_model_answer(str(scenario.get("raw_answer") or ""), state)


def _evaluate_grounding_context(scenario: dict[str, Any]) -> str:
    runtime = _runtime([])
    state = AgentExecutionState(
        message=str(scenario.get("message") or ""),
        memory_summaries=scenario.get("memory_summaries", []),
        citations=scenario.get("citations", []),
        observations=scenario.get("observations", []),
    )
    return "\n\n".join(runtime._answer_context(state))


def _runtime(tools: list[RegisteredTool]) -> AgentRuntime:
    return AgentRuntime(
        session=None,  # type: ignore[arg-type]
        plugin_registry=EvalRegistry(tools),  # type: ignore[arg-type]
        model_gateway=EvalGateway(),  # type: ignore[arg-type]
        rag_service=NullRag(),  # type: ignore[arg-type]
    )


def _tool_from_spec(spec: dict[str, Any]) -> RegisteredTool:
    name = str(spec["name"])
    manifest = PluginManifest(
        name=name,
        description=str(spec.get("description") or f"Evaluation tool {name}"),
        permission=str(spec.get("permission") or "read"),
        requires_confirmation=bool(spec.get("requires_confirmation")),
        parameters=spec.get("parameters") or {"type": "object"},
        entrypoint="eval:noop",
    )
    return RegisteredTool(
        manifest=manifest,
        handler=lambda **_: None,
        base_dir=Path("."),
        provider=str(spec.get("provider") or "local_plugin"),
        provider_tool_id=str(spec.get("provider_tool_id") or name),
        server_name=spec.get("server_name"),
    )


def _expectation_failures(actual: Any, expected: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if "equals" in expected and actual != expected["equals"]:
        failures.append(f"expected exact value {expected['equals']!r}, got {actual!r}")
    if "subset" in expected:
        _compare_subset(actual, expected["subset"], "actual", failures)
    actual_text = actual if isinstance(actual, str) else json.dumps(actual, ensure_ascii=False)
    for fragment in expected.get("contains", []):
        if str(fragment) not in actual_text:
            failures.append(f"missing required fragment {fragment!r}")
    lowered = actual_text.casefold()
    for fragment in expected.get("not_contains", []):
        if str(fragment).casefold() in lowered:
            failures.append(f"found forbidden fragment {fragment!r}")
    return failures


def _compare_subset(actual: Any, expected: Any, path: str, failures: list[str]) -> None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            failures.append(f"{path} should be an object")
            return
        for key, value in expected.items():
            if key not in actual:
                failures.append(f"{path}.{key} is missing")
                continue
            _compare_subset(actual[key], value, f"{path}.{key}", failures)
        return
    if actual != expected:
        failures.append(f"{path} expected {expected!r}, got {actual!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run offline AgentDemo behavior evaluations.")
    parser.add_argument("--suite", type=Path, help="Path to an alternate scenario JSON file.")
    parser.add_argument("--json", action="store_true", help="Print the full JSON report.")
    args = parser.parse_args(argv)
    report = asyncio.run(evaluate_suite(load_suite(args.suite)))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for result in report["results"]:
            label = "PASS" if result["passed"] else "FAIL"
            print(f"{label} {result['id']} [{result['category']}]")
            for failure in result["failures"]:
                print(f"  - {failure}")
        print(
            f"Score: {report['score']:.1%} ({report['passed']}/{report['total']}); "
            f"required: {report['minimum_score']:.1%}"
        )
    return 0 if report["score"] >= report["minimum_score"] else 1
