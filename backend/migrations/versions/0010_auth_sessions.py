"""Expiring sessions and shared authentication admission limits."""

import sqlalchemy as sa
from alembic import op

revision = "0010_auth_sessions"
down_revision = "0009_task_fencing"
branch_labels = depends_on = None


def upgrade() -> None:
    op.add_column("user_sessions", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user_sessions", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user_sessions", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    # Preserve recent sessions, but never renew the lifetime of an old token.
    op.execute("UPDATE user_sessions SET expires_at = created_at + INTERVAL '7 days', "
               "last_seen_at = created_at")
    op.alter_column("user_sessions", "expires_at", nullable=False)
    op.alter_column("user_sessions", "last_seen_at", nullable=False)
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])
    op.create_table("auth_rate_limits", sa.Column("key", sa.String(64), primary_key=True),
                    sa.Column("count", sa.Integer(), nullable=False),
                    sa.Column("reset_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_auth_rate_limits_reset_at", "auth_rate_limits", ["reset_at"])


def downgrade() -> None:
    op.drop_table("auth_rate_limits")
    op.drop_index("ix_user_sessions_expires_at", table_name="user_sessions")
    for name in ("revoked_at", "last_seen_at", "expires_at"):
        op.drop_column("user_sessions", name)
