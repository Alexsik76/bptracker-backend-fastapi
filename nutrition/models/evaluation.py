from datetime import date as dt_date
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
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


class MenuEvaluationCriterionBase(SQLModel):
    version: int = Field(sa_column=Column(Integer, nullable=False))
    key: str = Field(sa_column=Column(String, nullable=False))
    title: str = Field(sa_column=Column(String, nullable=False))
    instructions: str = Field(sa_column=Column(Text, nullable=False))
    criteria: dict[str, str] = Field(
        sa_column=Column(JSONB(astext_type=Text()), nullable=False),
    )
    position: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default=text("0")),
    )


class MenuEvaluationCriterion(MenuEvaluationCriterionBase, table=True):
    __tablename__: ClassVar[str] = "menu_evaluation_criteria"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
        ),
    )

    __table_args__ = (
        UniqueConstraint(
            "version", "key", name="uq_menu_evaluation_criteria_version_key"
        ),
    )


class MenuDayEvaluationBase(SQLModel):
    date: dt_date = Field(sa_column=Column(Date, nullable=False, index=True))
    criterion_key: str = Field(sa_column=Column(String, nullable=False))
    criteria_version: int = Field(sa_column=Column(Integer, nullable=False))
    choice: str = Field(sa_column=Column(String, nullable=False))
    confidence: float = Field(sa_column=Column(Float, nullable=False))
    probabilities: dict[str, float] = Field(
        sa_column=Column(JSONB(astext_type=Text()), nullable=False),
    )
    model: str = Field(sa_column=Column(String, nullable=False))
    menu_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            Uuid,
            ForeignKey("menus.id", ondelete="CASCADE"),
            index=True,
            nullable=True,
        ),
    )


class MenuDayEvaluation(MenuDayEvaluationBase, table=True):
    __tablename__: ClassVar[str] = "menu_day_evaluations"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    user_id: UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("users.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        ),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
        ),
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "date",
            "criterion_key",
            "menu_id",
            postgresql_nulls_not_distinct=True,
            name="uq_menu_day_evaluations_user_date_criterion_key_menu_id",
        ),
    )


class MenuEvaluationRead(SQLModel):
    key: str
    title: str
    level: str
    confidence: float
    criteria_version: int
    evaluated_at: datetime
    menu_id: UUID | None = None


class MealEvaluationRead(SQLModel):
    criterion_key: str
    choice: str
    level: str
    key: str = ""

    def __init__(self, **data: Any):
        if "criterion_key" in data and not data.get("key"):
            data["key"] = data["criterion_key"]
        elif "key" in data and not data.get("criterion_key"):
            data["criterion_key"] = data["key"]
        super().__init__(**data)
