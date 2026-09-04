"""Add scheduled tasks and reliable background execution fields.

Revision ID: 0008_task_schedules
Revises: 0007_agent_task_execution
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_task_schedules"
down_revision: str | None = "0007_agent_task_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_schedules",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("schedule_kind", sa.String(length=32), nullable=False),
        sa.Column("timezone", sa.String(length=80), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=True),
        sa.Column("daily_time", sa.String(length=5), nullable=True),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_task_id", sa.UUID(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_task_schedules_conversation_id_conversations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_task_id"],
            ["tasks.id"],
            name=op.f("fk_task_schedules_last_task_id_tasks"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_task_schedules_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_schedules")),
    )
    op.create_index(op.f("ix_task_schedules_user_id"), "task_schedules", ["user_id"])
    op.create_index(
        op.f("ix_task_schedules_conversation_id"), "task_schedules", ["conversation_id"]
    )
    op.create_index(
        op.f("ix_task_schedules_schedule_kind"), "task_schedules", ["schedule_kind"]
    )
    op.create_index(op.f("ix_task_schedules_next_run_at"), "task_schedules", ["next_run_at"])
    op.create_index(op.f("ix_task_schedules_enabled"), "task_schedules", ["enabled"])

    op.add_column("tasks", sa.Column("schedule_id", sa.UUID(), nullable=True))
    op.add_column("tasks", sa.Column("idempotency_key", sa.String(length=200), nullable=True))
    op.add_column(
        "tasks", sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "tasks", sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False)
    )
    op.add_column("tasks", sa.Column("next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("tasks", sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
    op.add_column("tasks", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.create_foreign_key(
        op.f("fk_tasks_schedule_id_task_schedules"),
        "tasks",
        "task_schedules",
        ["schedule_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_tasks_schedule_id"), "tasks", ["schedule_id"])
    op.create_index(op.f("ix_tasks_next_attempt_at"), "tasks", ["next_attempt_at"])
    op.create_index(op.f("ix_tasks_lease_expires_at"), "tasks", ["lease_expires_at"])
    op.create_unique_constraint(
        op.f("uq_tasks_user_id"), "tasks", ["user_id", "idempotency_key"]
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_tasks_user_id"), "tasks", type_="unique")
    op.drop_index(op.f("ix_tasks_lease_expires_at"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_next_attempt_at"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_schedule_id"), table_name="tasks")
    op.drop_constraint(op.f("fk_tasks_schedule_id_task_schedules"), "tasks", type_="foreignkey")
    op.drop_column("tasks", "lease_expires_at")
    op.drop_column("tasks", "heartbeat_at")
    op.drop_column("tasks", "next_attempt_at")
    op.drop_column("tasks", "max_attempts")
    op.drop_column("tasks", "attempt_count")
    op.drop_column("tasks", "idempotency_key")
    op.drop_column("tasks", "schedule_id")

    op.drop_index(op.f("ix_task_schedules_enabled"), table_name="task_schedules")
    op.drop_index(op.f("ix_task_schedules_next_run_at"), table_name="task_schedules")
    op.drop_index(op.f("ix_task_schedules_schedule_kind"), table_name="task_schedules")
    op.drop_index(op.f("ix_task_schedules_conversation_id"), table_name="task_schedules")
    op.drop_index(op.f("ix_task_schedules_user_id"), table_name="task_schedules")
    op.drop_table("task_schedules")
