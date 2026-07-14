"""drop_password_hash_from_users

Revision ID: 46a937daa5e2
Revises: 360593de6e48
Create Date: 2026-07-14 14:36:30.189815

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "46a937daa5e2"
down_revision: str | Sequence[str] | None = "360593de6e48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("users", "password_hash")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("users", sa.Column("password_hash", sa.String(), nullable=True))
