"""Fence background execution and record scheduling decisions.

Revision ID: 0009_task_fencing
Revises: 0008_task_schedules
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_task_fencing"
down_revision = "0008_task_schedules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("run_token", sa.UUID(), nullable=True))
    op.add_column("tasks", sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="180"))
    op.add_column("tasks", sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("replay_blocked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("replay_blocked_reason", sa.String(200), nullable=True))
    # Legacy running tasks may already have performed external work. Fence them
    # conservatively on upgrade; migration never silently replays an old worker.
    op.execute("UPDATE tasks SET replay_blocked_at = CURRENT_TIMESTAMP, "
               "replay_blocked_reason = 'Legacy execution present during upgrade' "
               "WHERE kind = 'agent' AND (status = 'running' OR "
               "(status = 'queued' AND EXISTS (SELECT 1 FROM tool_calls WHERE tool_calls.task_id = tasks.id)))")
    op.execute("UPDATE tasks SET deadline_at = CURRENT_TIMESTAMP + INTERVAL '15 minutes' "
               "WHERE kind = 'agent' AND status IN ('queued', 'running')")
    op.create_index("ix_tasks_ready_queue", "tasks", ["next_attempt_at", "created_at", "id"],
                    postgresql_where=sa.text("kind = 'agent' AND status = 'queued'"))
    op.add_column("task_schedules", sa.Column("misfire_grace_seconds", sa.Integer(), nullable=False, server_default="300"))
    op.add_column("task_schedules", sa.Column("last_dispatch_status", sa.String(32), nullable=True))
    op.add_column("task_schedules", sa.Column("last_dispatch_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("task_schedules", sa.Column("last_dispatch_error", sa.Text(), nullable=True))


def downgrade() -> None:
    for name in ("last_dispatch_error", "last_dispatch_at", "last_dispatch_status", "misfire_grace_seconds"):
        op.drop_column("task_schedules", name)
    op.drop_index("ix_tasks_ready_queue", table_name="tasks")
    for name in ("replay_blocked_reason", "replay_blocked_at", "deadline_at", "timeout_seconds", "run_token"):
        op.drop_column("tasks", name)
