"""add_on_delete_cascade_to_users

Revision ID: 360593de6e48
Revises: 8809aea66172
Create Date: 2026-07-14 14:30:59.852948

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "360593de6e48"
down_revision: str | Sequence[str] | None = "8809aea66172"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("sessions_user_id_fkey", "sessions", type_="foreignkey")
    op.create_foreign_key(
        "sessions_user_id_fkey",
        "sessions",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "webauthn_credentials_user_id_fkey", "webauthn_credentials", type_="foreignkey"
    )
    op.create_foreign_key(
        "webauthn_credentials_user_id_fkey",
        "webauthn_credentials",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("fk_measurements_user_id_users", "measurements", type_="foreignkey")
    op.create_foreign_key(
        "fk_measurements_user_id_users",
        "measurements",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("fk_prescriptions_user_id_users", "prescriptions", type_="foreignkey")
    op.create_foreign_key(
        "fk_prescriptions_user_id_users",
        "prescriptions",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("fk_intake_reports_user_id_users", "intake_reports", type_="foreignkey")
    op.create_foreign_key(
        "fk_intake_reports_user_id_users",
        "intake_reports",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("fk_reminder_config_user_id_users", "reminder_config", type_="foreignkey")
    op.create_foreign_key(
        "fk_reminder_config_user_id_users",
        "reminder_config",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("email_outbox_user_id_fkey", "email_outbox", type_="foreignkey")
    op.create_foreign_key(
        "email_outbox_user_id_fkey",
        "email_outbox",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("email_outbox_user_id_fkey", "email_outbox", type_="foreignkey")
    op.create_foreign_key(
        "email_outbox_user_id_fkey",
        "email_outbox",
        "users",
        ["user_id"],
        ["id"],
    )
    op.drop_constraint("fk_reminder_config_user_id_users", "reminder_config", type_="foreignkey")
    op.create_foreign_key(
        "fk_reminder_config_user_id_users",
        "reminder_config",
        "users",
        ["user_id"],
        ["id"],
    )
    op.drop_constraint("fk_intake_reports_user_id_users", "intake_reports", type_="foreignkey")
    op.create_foreign_key(
        "fk_intake_reports_user_id_users",
        "intake_reports",
        "users",
        ["user_id"],
        ["id"],
    )
    op.drop_constraint("fk_prescriptions_user_id_users", "prescriptions", type_="foreignkey")
    op.create_foreign_key(
        "fk_prescriptions_user_id_users",
        "prescriptions",
        "users",
        ["user_id"],
        ["id"],
    )
    op.drop_constraint("fk_measurements_user_id_users", "measurements", type_="foreignkey")
    op.create_foreign_key(
        "fk_measurements_user_id_users",
        "measurements",
        "users",
        ["user_id"],
        ["id"],
    )
    op.drop_constraint(
        "webauthn_credentials_user_id_fkey", "webauthn_credentials", type_="foreignkey"
    )
    op.create_foreign_key(
        "webauthn_credentials_user_id_fkey",
        "webauthn_credentials",
        "users",
        ["user_id"],
        ["id"],
    )
    op.drop_constraint("sessions_user_id_fkey", "sessions", type_="foreignkey")
    op.create_foreign_key(
        "sessions_user_id_fkey",
        "sessions",
        "users",
        ["user_id"],
        ["id"],
    )
