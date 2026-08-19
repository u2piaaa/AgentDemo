"""Add durable agent task execution fields.

Revision ID: 0007_agent_task_execution
Revises: 0006_knowledge_user_sources
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_agent_task_execution"
down_revision: str | None = "0006_knowledge_user_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("kind", sa.String(length=32), server_default="manual", nullable=False),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "input",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("tasks", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_tasks_kind"), "tasks", ["kind"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_tasks_kind"), table_name="tasks")
    op.drop_column("tasks", "finished_at")
    op.drop_column("tasks", "started_at")
    op.drop_column("tasks", "input")
    op.drop_column("tasks", "kind")
