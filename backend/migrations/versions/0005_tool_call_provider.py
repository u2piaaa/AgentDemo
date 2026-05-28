"""Add provider to tool-call audits.

Revision ID: 0005_tool_call_provider
Revises: 0004_task_ownership_audit
Create Date: 2026-05-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_tool_call_provider"
down_revision: str | None = "0004_task_ownership_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tool_calls",
        sa.Column("provider", sa.String(length=40), nullable=False, server_default="local_plugin"),
    )
    op.create_index(op.f("ix_tool_calls_provider"), "tool_calls", ["provider"], unique=False)
    op.alter_column("tool_calls", "provider", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_tool_calls_provider"), table_name="tool_calls")
    op.drop_column("tool_calls", "provider")
