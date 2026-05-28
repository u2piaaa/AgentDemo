"""Add knowledge user and MCP source metadata.

Revision ID: 0006_knowledge_user_sources
Revises: 0005_tool_call_provider
Create Date: 2026-05-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_knowledge_user_sources"
down_revision: str | None = "0005_tool_call_provider"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("knowledge_documents", sa.Column("user_id", sa.UUID(), nullable=True))
    op.create_index(op.f("ix_knowledge_documents_user_id"), "knowledge_documents", ["user_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_knowledge_documents_user_id_users"),
        "knowledge_documents",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_knowledge_documents_user_id_users"), "knowledge_documents", type_="foreignkey")
    op.drop_index(op.f("ix_knowledge_documents_user_id"), table_name="knowledge_documents")
    op.drop_column("knowledge_documents", "user_id")
