"""add cooked_weight_g to dishes

Revision ID: e135a2b3c4d5
Revises: d131a2b3c4d5
Create Date: 2026-11-03 12:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e135a2b3c4d5"
down_revision: str | Sequence[str] | None = "d131a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("dishes", sa.Column("cooked_weight_g", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("dishes", "cooked_weight_g")
