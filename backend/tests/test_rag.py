from app.services.rag import RagService


def test_split_text_uses_overlap() -> None:
    service = RagService(session=None)  # type: ignore[arg-type]
    chunks = service._split_text("a" * 1000, chunk_size=500, overlap=50)

    assert len(chunks) == 3
    assert chunks[1].startswith("a" * 50)
