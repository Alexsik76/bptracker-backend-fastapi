"""fk_magic_links_email_users_email

Revision ID: d71680345544
Revises: e1a2b3c4d5e6
Create Date: 2026-09-27 21:54:46.569249

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d71680345544"
down_revision: str | Sequence[str] | None = "e1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("DELETE FROM magic_links WHERE email NOT IN (SELECT email FROM users)")
    op.create_foreign_key(
        "magic_links_email_fkey",
        "magic_links",
        "users",
        ["email"],
        ["email"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("magic_links_email_fkey", "magic_links", type_="foreignkey")
