import pytest

from app.evals.runner import evaluate_suite, load_suite


@pytest.mark.asyncio
async def test_offline_agent_eval_suite_meets_quality_gate() -> None:
    report = await evaluate_suite(load_suite())

    failures = [result for result in report["results"] if not result["passed"]]
    assert report["total"] >= 10
    assert report["score"] >= report["minimum_score"], failures
    assert all(category["score"] == 1.0 for category in report["categories"].values())
