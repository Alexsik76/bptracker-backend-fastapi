"""add prompt and duration_ms to generation_log

Revision ID: 31a87e24b5d6
Revises: c339f69cc97a
Create Date: 2026-10-26 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "31a87e24b5d6"
down_revision: str | Sequence[str] | None = "c339f69cc97a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("generation_log", sa.Column("prompt", sa.Text(), nullable=True))
    op.add_column("generation_log", sa.Column("duration_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("generation_log", "duration_ms")
    op.drop_column("generation_log", "prompt")
