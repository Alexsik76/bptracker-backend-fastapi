"""create ocr training tables

Revision ID: f1e2d3c4b5a6
Revises: e4f5a6b7c8d9
Create Date: 2026-10-31 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1e2d3c4b5a6"
down_revision: str | Sequence[str] | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ocr_samples",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("photo_path", sa.String(), nullable=False),
        sa.Column("ref_sys", sa.Integer(), nullable=True),
        sa.Column("ref_dia", sa.Integer(), nullable=True),
        sa.Column("ref_pul", sa.Integer(), nullable=True),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_model", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("session", sa.String(), nullable=True),
        sa.Column(
            "corrected_by_user",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("display_box", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("digit_boxes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("label_source", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ocr_samples_photo_path"),
        "ocr_samples",
        ["photo_path"],
        unique=True,
    )
    op.create_index(
        op.f("ix_ocr_samples_session"),
        "ocr_samples",
        ["session"],
        unique=False,
    )

    op.create_table(
        "ocr_datasets",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "test_sessions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_ocr_datasets_name"),
    )

    op.create_table(
        "ocr_dataset_samples",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("sample_id", sa.Uuid(), nullable=False),
        sa.Column("split", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["ocr_datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sample_id"], ["ocr_samples.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dataset_id", "sample_id", name="uq_ocr_dataset_samples_dataset_sample"
        ),
    )
    op.create_index(
        op.f("ix_ocr_dataset_samples_dataset_id"),
        "ocr_dataset_samples",
        ["dataset_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ocr_dataset_samples_sample_id"),
        "ocr_dataset_samples",
        ["sample_id"],
        unique=False,
    )

    op.create_table(
        "ocr_training_jobs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.String(),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("model_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["dataset_id"], ["ocr_datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ocr_training_jobs_dataset_id"),
        "ocr_training_jobs",
        ["dataset_id"],
        unique=False,
    )

    op.create_table(
        "ocr_models",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("display_model_path", sa.String(), nullable=False),
        sa.Column("digit_model_path", sa.String(), nullable=False),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "is_current",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["ocr_training_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version", name="uq_ocr_models_version"),
    )
    op.create_index(
        op.f("ix_ocr_models_job_id"),
        "ocr_models",
        ["job_id"],
        unique=False,
    )
    op.create_index(
        "uq_ocr_models_is_current",
        "ocr_models",
        ["is_current"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )

    op.create_foreign_key(
        "fk_ocr_training_jobs_model_id_ocr_models",
        "ocr_training_jobs",
        "ocr_models",
        ["model_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_ocr_training_jobs_model_id_ocr_models",
        "ocr_training_jobs",
        type_="foreignkey",
    )
    op.drop_index(
        "uq_ocr_models_is_current",
        table_name="ocr_models",
        postgresql_where=sa.text("is_current = true"),
    )
    op.drop_index(op.f("ix_ocr_models_job_id"), table_name="ocr_models")
    op.drop_table("ocr_models")
    op.drop_index(
        op.f("ix_ocr_training_jobs_dataset_id"),
        table_name="ocr_training_jobs",
    )
    op.drop_table("ocr_training_jobs")
    op.drop_index(
        op.f("ix_ocr_dataset_samples_sample_id"),
        table_name="ocr_dataset_samples",
    )
    op.drop_index(
        op.f("ix_ocr_dataset_samples_dataset_id"),
        table_name="ocr_dataset_samples",
    )
    op.drop_table("ocr_dataset_samples")
    op.drop_table("ocr_datasets")
    op.drop_index(op.f("ix_ocr_samples_session"), table_name="ocr_samples")
    op.drop_index(op.f("ix_ocr_samples_photo_path"), table_name="ocr_samples")
    op.drop_table("ocr_samples")
