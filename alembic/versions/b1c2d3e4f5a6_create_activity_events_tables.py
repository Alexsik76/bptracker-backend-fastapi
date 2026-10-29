"""create activity events tables

Revision ID: b1c2d3e4f5a6
Revises: a2b3c4d5e6f7
Create Date: 2026-10-29 21:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "request_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("client", sa.String(), nullable=False),
        sa.Column("module", sa.String(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("route", sa.String(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_request_events_occurred_at"), "request_events", ["occurred_at"], unique=False
    )
    op.create_index(
        op.f("ix_request_events_user_id_occurred_at"),
        "request_events",
        ["user_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_request_events_module_occurred_at"),
        "request_events",
        ["module", "occurred_at"],
        unique=False,
    )

    op.create_table(
        "error_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("route", sa.String(), nullable=False),
        sa.Column("exception_type", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("traceback", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_error_events_occurred_at"), "error_events", ["occurred_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_error_events_occurred_at"), table_name="error_events")
    op.drop_table("error_events")
    op.drop_index(op.f("ix_request_events_module_occurred_at"), table_name="request_events")
    op.drop_index(op.f("ix_request_events_user_id_occurred_at"), table_name="request_events")
    op.drop_index(op.f("ix_request_events_occurred_at"), table_name="request_events")
    op.drop_table("request_events")
