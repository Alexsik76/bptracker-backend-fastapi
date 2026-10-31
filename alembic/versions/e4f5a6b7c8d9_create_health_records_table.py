"""create health records table

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-10-31 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4f5a6b7c8d9"
down_revision: str | Sequence[str] | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "health_records",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("record_type", sa.String(), nullable=False),
        sa.Column("values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_origin", sa.String(), nullable=False),
        sa.Column("hc_record_id", sa.String(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "hc_record_id", name="uq_health_records_user_hc_record"),
    )
    op.create_index(
        op.f("ix_health_records_user_id"),
        "health_records",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_health_records_user_id_record_type_start_time",
        "health_records",
        ["user_id", "record_type", "start_time"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_health_records_user_id_record_type_start_time",
        table_name="health_records",
    )
    op.drop_index(
        op.f("ix_health_records_user_id"),
        table_name="health_records",
    )
    op.drop_table("health_records")
