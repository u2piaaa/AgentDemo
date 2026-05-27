"""Add task ownership and tool-call audit fields.

Revision ID: 0004_task_ownership_audit
Revises: 0003_users_owners
Create Date: 2026-05-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_task_ownership_audit"
down_revision: str | None = "0003_users_owners"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("user_id", sa.UUID(), nullable=True))
    op.add_column("tasks", sa.Column("trace_id", sa.String(length=80), nullable=True))
    op.create_index(op.f("ix_tasks_user_id"), "tasks", ["user_id"], unique=False)
    op.create_index(op.f("ix_tasks_trace_id"), "tasks", ["trace_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_tasks_user_id_users"),
        "tasks",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.add_column("tool_calls", sa.Column("user_id", sa.UUID(), nullable=True))
    op.add_column("tool_calls", sa.Column("input_summary", sa.Text(), nullable=True))
    op.create_index(op.f("ix_tool_calls_user_id"), "tool_calls", ["user_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_tool_calls_user_id_users"),
        "tool_calls",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_tool_calls_user_id_users"), "tool_calls", type_="foreignkey")
    op.drop_index(op.f("ix_tool_calls_user_id"), table_name="tool_calls")
    op.drop_column("tool_calls", "input_summary")
    op.drop_column("tool_calls", "user_id")

    op.drop_constraint(op.f("fk_tasks_user_id_users"), "tasks", type_="foreignkey")
    op.drop_index(op.f("ix_tasks_trace_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_user_id"), table_name="tasks")
    op.drop_column("tasks", "trace_id")
    op.drop_column("tasks", "user_id")
