import asyncio
import json

import httpx
import pytest

from app.core.config import Settings
from app.services.retrieval_reranker import RetrievalReranker, diverse_indices


def settings(**overrides):
    return Settings(
        rag_rerank_mode="api",
        rag_rerank_url="https://reranker.example/v1/rerank",
        rag_rerank_api_key="synthetic-test-key",
        **overrides,
    )


async def test_listwise_reranker_uses_bounded_payload_and_validates_candidate_indices():
    async def respond(request):
        body = json.loads(request.content)
        assert body["model"] == "jina-reranker-v3.5"
        assert len(body["query"]) == 2000
        assert len(body["documents"][0]) == 2000
        assert body["top_n"] == 2
        assert body["return_documents"] is False
        assert request.headers["Authorization"] == "Bearer synthetic-test-key"
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 0, "relevance_score": 0.2},
                    {"index": 1, "relevance_score": 0.9},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        ranked = await RetrievalReranker(settings(), client).rank("q" * 3000, ["a" * 3000, "b"])
    assert ranked == [(1, 0.9), (0, 0.2)]


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"index": 0, "relevance_score": 1}],
        [{"index": 0, "relevance_score": 1}, {"index": 0, "relevance_score": 0.2}],
        [{"index": 0, "relevance_score": 1}, {"index": 10, "relevance_score": 0.2}],
        [{"index": 0, "relevance_score": 1}, {"index": 1, "relevance_score": "secret"}],
        [{"index": 0, "relevance_score": 1}, {"index": True, "relevance_score": 0.2}],
    ],
)
async def test_malformed_reranking_never_replaces_local_order(rows):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"results": rows}))
    ) as client:
        assert (
            await RetrievalReranker(settings(), client).rank("query", ["first", "second"]) is None
        )


@pytest.mark.parametrize(
    "status,body",
    [
        (503, b"provider failure"),
        (200, b"x" * 262145),
        (200, b'{"results":[{"index":0,"relevance_score":NaN},{"index":1,"relevance_score":1}]}'),
    ],
    ids=["unavailable", "oversized", "nonfinite"],
)
async def test_provider_failure_or_oversized_response_keeps_fallback(status, body):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, content=body))
    ) as client:
        assert (
            await RetrievalReranker(settings(), client).rank("query", ["first", "second"]) is None
        )


async def test_reranker_timeout_is_bounded_and_cancellation_propagates():
    entered = asyncio.Event()

    async def slow(request):
        entered.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
        assert (
            await RetrievalReranker(settings(rag_rerank_timeout_seconds=0.01), client).rank(
                "q", ["a", "b"]
            )
            is None
        )
        entered.clear()
        task = asyncio.create_task(RetrievalReranker(settings(), client).rank("q", ["a", "b"]))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_default_local_mode_and_oversized_candidate_list_make_no_network_calls():
    def forbidden(request):
        pytest.fail("Network call is not allowed")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        assert await RetrievalReranker(Settings(), client).rank("q", ["a", "b"]) is None
        assert await RetrievalReranker(settings(), client).rank("q", ["a"] * 25) is None


async def test_reranker_does_not_follow_redirects_with_credentials():
    calls = []

    def redirect(request):
        calls.append(request.url)
        return httpx.Response(307, headers={"Location": "https://other.example/v1/rerank"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(redirect), follow_redirects=True
    ) as client:
        assert await RetrievalReranker(settings(), client).rank("q", ["a", "b"]) is None
    assert len(calls) == 1


def test_mmr_omits_duplicate_evidence_and_preserves_distinct_facts():
    first = "Aurora fault recovery: restart the worker only after clearing the queue. " * 10
    other = "银河恢复口令是星河-73，数据库需要单独备份。"
    assert diverse_indices([first, first, other], 3) == [0, 2]
    assert diverse_indices([], 4) == []


def test_mmr_promotes_complementary_evidence_over_heavily_overlapping_chunks():
    text = "The deployment guide describes queue recovery and worker startup procedures. " * 5
    texts = [
        text,
        text + "Added note.",
        "Database restore needs a verified backup.",
        "Unrelated note.",
    ]
    assert diverse_indices(texts, 2) == [0, 2]


def test_mmr_preserves_identical_wording_about_different_entities():
    text = "Revenue increased by 3 percent."
    assert diverse_indices([text, text], 2, ["Company A", "Company B"]) == [0, 1]
