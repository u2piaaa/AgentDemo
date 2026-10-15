"""Bounded Jina-compatible listwise reranking, opt-in for hosted or local APIs."""

import asyncio
import json
import math

import httpx

from app.core.config import Settings

MAX_RESPONSE_BYTES = 262144


class RetrievalReranker:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings, self.client = settings, client

    async def rank(self, query: str, documents: list[str]) -> list[tuple[int, float]] | None:
        if (
            self.settings.rag_rerank_mode != "api"
            or not self.settings.rag_rerank_url
            or not 2 <= len(documents) <= self.settings.rag_rerank_candidates
        ):
            return None
        try:
            timeout = min(
                self.settings.rag_rerank_timeout_seconds,
                self.settings.agent_retrieval_timeout_seconds / 4,
            )
            async with asyncio.timeout(timeout):
                if self.client is not None:
                    return await self._request(self.client, query, documents)
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                    return await self._request(client, query, documents)
        except Exception:
            # No retries or provider text in logs: keep the local retrieval result.
            return None

    async def _request(
        self, client: httpx.AsyncClient, query: str, documents: list[str]
    ) -> list[tuple[int, float]]:
        headers = (
            {"Authorization": f"Bearer {self.settings.rag_rerank_api_key}"}
            if self.settings.rag_rerank_api_key
            else {}
        )
        payload = {
            "model": self.settings.rag_rerank_model,
            "query": query[:2000],
            "documents": [text[:2000] for text in documents],
            "top_n": len(documents),
            "return_documents": False,
        }
        async with client.stream(
            "POST",
            self.settings.rag_rerank_url,
            json=payload,
            headers=headers,
            follow_redirects=False,
        ) as response:
            response.raise_for_status()
            raw = bytearray()
            async for block in response.aiter_bytes():
                raw.extend(block)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError("Reranking response too large")
        data = json.loads(raw)
        rows = data.get("results")
        if not isinstance(rows, list) or len(rows) != len(documents):
            raise ValueError("Incomplete reranking response")
        ranked, seen = [], set()
        for row in rows:
            index, score = row.get("index"), row.get("relevance_score")
            if (
                type(index) is not int
                or index not in range(len(documents))
                or index in seen
                or type(score) not in (float, int)
                or not math.isfinite(score)
            ):
                raise ValueError("Invalid reranking score or index")
            seen.add(index)
            ranked.append((index, float(score)))
        return sorted(ranked, key=lambda item: (-item[1], item[0]))


def diverse_indices(texts: list[str], limit: int, contexts: list[str] | None = None) -> list[int]:
    """MMR with character-shingle similarity; multilingual and fully local.

    Ranking provides relevance, shingle Jaccard supplies redundancy. Exact and
    almost identical evidence is omitted instead of filling the context twice.
    """
    fingerprints = []
    for text in texts:
        clean = " ".join(text.casefold().split())[:3000]
        fingerprints.append({clean[i : i + 3] for i in range(max(1, len(clean) - 2))})
    selected: list[int] = []
    remaining = list(range(len(texts)))
    while remaining and len(selected) < limit:
        ranked = []
        for index in remaining:
            redundancy = max(
                (
                    len(fingerprints[index] & fingerprints[other])
                    / max(len(fingerprints[index] | fingerprints[other]), 1)
                    for other in selected
                ),
                default=0.0,
            )
            # Identical wording can refer to different entities/sections. Only
            # omit duplicates when their source context is also the same.
            duplicate_context = contexts is None or any(
                contexts[index] == contexts[other]
                and len(fingerprints[index] & fingerprints[other])
                / max(len(fingerprints[index] | fingerprints[other]), 1)
                >= 0.95
                for other in selected
            )
            if redundancy >= 0.95 and duplicate_context:
                continue
            relevance = 1 - index / max(len(texts), 1)
            ranked.append((0.7 * relevance - 0.3 * redundancy, -index, index))
        if not ranked:
            break
        chosen = max(ranked)[2]
        selected.append(chosen)
        remaining.remove(chosen)
    return selected
