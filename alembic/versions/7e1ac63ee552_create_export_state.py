"""create_export_state

Revision ID: 7e1ac63ee552
Revises: d71680345544
Create Date: 2026-09-27 21:58:08.352901

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7e1ac63ee552"
down_revision: str | Sequence[str] | None = "d71680345544"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "export_state",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("last_export_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.execute(
        "INSERT INTO export_state (user_id, last_export_at) "
        "SELECT id, last_export_at FROM users WHERE last_export_at IS NOT NULL"
    )
    op.drop_column("users", "last_export_at")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "users",
        sa.Column("last_export_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE users SET last_export_at = export_state.last_export_at "
        "FROM export_state WHERE users.id = export_state.user_id"
    )
    op.drop_table("export_state")
