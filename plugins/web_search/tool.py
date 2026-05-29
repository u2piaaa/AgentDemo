from app.services.web_search import search_web


def run(query: str, max_results: int | None = None, recency_days: int | None = None) -> dict:
    return search_web(query, max_results=max_results, recency_days=recency_days)
