"""create_menu_notes_and_off_plan_entries

Revision ID: c339f69cc97a
Revises: e4f62ae07b5f
Create Date: 2026-10-23 21:51:32.541960

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c339f69cc97a"
down_revision: str | Sequence[str] | None = "e4f62ae07b5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. menu_notes
    op.create_table(
        "menu_notes",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("info", "correction", name="menunotekind"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_menu_notes_date"), "menu_notes", ["date"], unique=False)
    op.create_index(op.f("ix_menu_notes_user_id"), "menu_notes", ["user_id"], unique=False)

    # 2. off_plan_entries
    op.create_table(
        "off_plan_entries",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("kcal_est", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_off_plan_entries_date"), "off_plan_entries", ["date"], unique=False)
    op.create_index(
        op.f("ix_off_plan_entries_user_id"), "off_plan_entries", ["user_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_off_plan_entries_user_id"), table_name="off_plan_entries")
    op.drop_index(op.f("ix_off_plan_entries_date"), table_name="off_plan_entries")
    op.drop_table("off_plan_entries")

    op.drop_index(op.f("ix_menu_notes_user_id"), table_name="menu_notes")
    op.drop_index(op.f("ix_menu_notes_date"), table_name="menu_notes")
    op.drop_table("menu_notes")
    sa.Enum(name="menunotekind").drop(op.get_bind(), checkfirst=True)
