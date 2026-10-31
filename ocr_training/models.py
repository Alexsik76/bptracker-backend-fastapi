from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class OcrSample(SQLModel, table=True):
    __tablename__: ClassVar[str] = "ocr_samples"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    photo_path: str = Field(sa_column=Column(String, nullable=False, unique=True, index=True))
    ref_sys: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    ref_dia: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    ref_pul: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    taken_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    device_model: str | None = Field(default=None, sa_column=Column(String, nullable=True))
    source: str | None = Field(default=None, sa_column=Column(String, nullable=True))
    session: str | None = Field(default=None, sa_column=Column(String, nullable=True, index=True))
    corrected_by_user: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    meta: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    display_box: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSONB(none_as_null=True), nullable=True)
    )
    digit_boxes: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(JSONB(none_as_null=True), nullable=True)
    )
    label_source: str | None = Field(default=None, sa_column=Column(String, nullable=True))
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        ),
    )


class OcrDataset(SQLModel, table=True):
    __tablename__: ClassVar[str] = "ocr_datasets"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    name: str = Field(sa_column=Column(String, nullable=False, unique=True))
    test_sessions: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )


class OcrDatasetSample(SQLModel, table=True):
    __tablename__: ClassVar[str] = "ocr_dataset_samples"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    dataset_id: UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("ocr_datasets.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    sample_id: UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("ocr_samples.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    split: str = Field(sa_column=Column(String, nullable=False))

    __table_args__ = (
        UniqueConstraint("dataset_id", "sample_id", name="uq_ocr_dataset_samples_dataset_sample"),
    )


class OcrTrainingJob(SQLModel, table=True):
    __tablename__: ClassVar[str] = "ocr_training_jobs"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    dataset_id: UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("ocr_datasets.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    status: str = Field(
        default="queued",
        sa_column=Column(String, nullable=False, server_default=text("'queued'")),
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )
    claimed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    finished_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    model_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            Uuid,
            ForeignKey(
                "ocr_models.id",
                ondelete="SET NULL",
                use_alter=True,
                name="fk_ocr_training_jobs_model_id_ocr_models",
            ),
            nullable=True,
        ),
    )


class OcrModel(SQLModel, table=True):
    __tablename__: ClassVar[str] = "ocr_models"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    version: str = Field(sa_column=Column(String, nullable=False, unique=True))
    job_id: UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("ocr_training_jobs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    display_model_path: str = Field(sa_column=Column(String, nullable=False))
    digit_model_path: str = Field(sa_column=Column(String, nullable=False))
    report: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    is_current: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )

    __table_args__ = (
        Index(
            "uq_ocr_models_is_current",
            "is_current",
            unique=True,
            postgresql_where=text("is_current = true"),
        ),
    )


# Pydantic / SQLModel Schemas for API


class SampleLabelsUpdate(SQLModel):
    display_box: dict[str, Any]
    digit_boxes: list[dict[str, Any]]


class SampleFilter(SQLModel):
    source: str | None = None
    session: str | None = None
    labeled: bool | None = None


class SampleListResponse(SQLModel):
    items: list[OcrSample]
    total: int
    limit: int
    offset: int


class DatasetCreate(SQLModel):
    name: str
    test_sessions: list[str] = Field(default_factory=list)
    source: str | None = None
    session: str | None = None
    labeled: bool | None = None
    sample_filter: dict[str, Any] | None = None


class DatasetDetailResponse(SQLModel):
    id: UUID
    name: str
    test_sessions: list[str]
    created_at: datetime
    total_samples: int = 0
    train_samples: int = 0
    val_samples: int = 0
    test_samples: int = 0


class JobCreate(SQLModel):
    dataset_id: UUID
    params: dict[str, Any] = Field(default_factory=dict)


class JobFailRequest(SQLModel):
    error: str


class ImportSummary(SQLModel):
    created: int
    updated: int
    skipped: int


class SampleManifestItem(SQLModel):
    sample_id: UUID
    split: str
    ref_sys: int | None = None
    ref_dia: int | None = None
    ref_pul: int | None = None
    display_box: dict[str, Any] | None = None
    digit_boxes: list[dict[str, Any]] | None = None
    photo_path: str
    photo_url: str


class ClaimJobResponse(SQLModel):
    job: OcrTrainingJob
    dataset_id: UUID
    manifest: list[SampleManifestItem]
    samples: list[SampleManifestItem]
