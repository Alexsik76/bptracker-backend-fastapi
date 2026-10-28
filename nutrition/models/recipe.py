from datetime import datetime
from typing import ClassVar
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlmodel import Field, SQLModel


class RecipeBase(SQLModel):
    name: str = Field(sa_column=Column(String, nullable=False))
    archived: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )


class Recipe(RecipeBase, table=True):
    __tablename__: ClassVar[str] = "recipes"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    user_id: UUID = Field(
        sa_column=Column(
            Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
        )
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        ),
    )

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_recipes_user_id_name"),
    )


class RecipeProduct(SQLModel, table=True):
    __tablename__: ClassVar[str] = "recipe_products"

    recipe_id: UUID = Field(
        sa_column=Column(
            Uuid, ForeignKey("recipes.id", ondelete="CASCADE"), primary_key=True, nullable=False
        )
    )
    product_id: UUID = Field(
        sa_column=Column(
            Uuid, ForeignKey("products.id", ondelete="CASCADE"), primary_key=True, nullable=False
        )
    )


class RecipeCreate(SQLModel):
    name: str
    product_ids: list[UUID] = []


class RecipeUpdate(SQLModel):
    name: str | None = None
    archived: bool | None = None


class RecipeRead(RecipeBase):
    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime
    product_ids: list[UUID] = []
