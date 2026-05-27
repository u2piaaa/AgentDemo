"""Bind knowledge documents to conversations.

Revision ID: 0002_conversation_documents
Revises: 0001_initial
Create Date: 2026-05-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_conversation_documents"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("knowledge_documents", sa.Column("conversation_id", sa.UUID(), nullable=True))
    op.create_index(
        op.f("ix_knowledge_documents_conversation_id"),
        "knowledge_documents",
        ["conversation_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_knowledge_documents_conversation_id_conversations"),
        "knowledge_documents",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_knowledge_documents_conversation_id_conversations"),
        "knowledge_documents",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_knowledge_documents_conversation_id"), table_name="knowledge_documents")
    op.drop_column("knowledge_documents", "conversation_id")
