from app.schemas import TaskCreate, TaskUpdate


def test_task_payload_defaults() -> None:
    payload = TaskCreate(name="Index document")

    assert payload.conversation_id is None
    assert payload.metadata == {}


def test_task_update_accepts_progress() -> None:
    payload = TaskUpdate(status="running", progress=50)

    assert payload.status == "running"
    assert payload.progress == 50
