"""decouple dish components from products

Revision ID: f140a2b3c4d5
Revises: e135a2b3c4d5
Create Date: 2026-11-04 11:30:00.000000

Downgrade note:
Downgrade re-adds `product_id` (nullable), restores it by matching `name`
case-insensitively against `products.name` for products belonging to the
same user (dish owner). For components with no matching product, `product_id`
is left NULL. This is the safest variant: it causes no data loss (no rows
deleted), creates no phantom products in the user's catalogue, and allows
the downgrade and foreign key restoration to complete without error.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f140a2b3c4d5"
down_revision: str | Sequence[str] | None = "e135a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add dish_components.name as nullable
    op.add_column(
        "dish_components",
        sa.Column("name", sa.String(length=200), nullable=True),
    )

    # 2. Fill name from products.name via product_id
    op.execute(
        """
        UPDATE dish_components
        SET name = products.name
        FROM products
        WHERE dish_components.product_id = products.id
        """
    )
    # Defensive fallback for any orphaned rows where name might still be NULL
    op.execute(
        """
        UPDATE dish_components
        SET name = 'Unknown'
        WHERE name IS NULL
        """
    )

    # 3. Set NOT NULL
    op.alter_column("dish_components", "name", nullable=False)

    # 4. Drop FK constraint, index, and product_id column
    op.drop_constraint(
        "fk_dish_components_product_id_products",
        "dish_components",
        type_="foreignkey",
    )
    op.drop_index("ix_dish_components_product_id", table_name="dish_components")
    op.drop_column("dish_components", "product_id")


def downgrade() -> None:
    # 1. Re-add product_id (nullable)
    op.add_column(
        "dish_components",
        sa.Column("product_id", sa.Uuid(), nullable=True),
    )

    # 2. Restore product_id by matching name to a product of the dish owner case-insensitively.
    # If no matching product is found, product_id remains NULL (safest variant).
    op.execute(
        """
        UPDATE dish_components dc
        SET product_id = (
            SELECT p.id
            FROM products p
            JOIN dishes d ON d.id = dc.dish_id
            WHERE p.user_id = d.user_id
              AND LOWER(p.name) = LOWER(dc.name)
            ORDER BY p.id
            LIMIT 1
        )
        """
    )

    # 3. Restore index on product_id
    op.create_index(
        "ix_dish_components_product_id",
        "dish_components",
        ["product_id"],
        unique=False,
    )

    # 4. Restore FK constraint with ON DELETE CASCADE
    op.create_foreign_key(
        "fk_dish_components_product_id_products",
        "dish_components",
        "products",
        ["product_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 5. Drop name column
    op.drop_column("dish_components", "name")
