from dataclasses import dataclass, field
from datetime import date, timedelta
from uuid import UUID

from sqlmodel.ext.asyncio.session import AsyncSession

from nutrition import crud
from nutrition.models import (
    DishKind,
    DishRead,
    MenuNoteKind,
    MenuRead,
    NutritionProfileRead,
    OffPlanEntryRead,
    ProductRead,
)


@dataclass
class GenerationContext:
    user_id: UUID
    date: date
    scope: str
    target_slot: str | None
    profile: NutritionProfileRead | None
    ready_dishes: list[DishRead]
    constant_dishes: list[DishRead]
    products: list[ProductRead]
    existing_day_menus: list[MenuRead]
    history_menus: list[MenuRead]
    corrections: list[str] = field(default_factory=list)
    off_plan_entries: list[OffPlanEntryRead] = field(default_factory=list)

    @property
    def dishes_map(self) -> dict[UUID, DishRead]:
        res = {}
        for d in self.ready_dishes:
            res[d.id] = d
        for d in self.constant_dishes:
            res[d.id] = d
        return res

    @property
    def products_map(self) -> dict[UUID, ProductRead]:
        return {p.id: p for p in self.products}


async def build_generation_context(
    session: AsyncSession,
    user_id: UUID,
    menu_date: date,
    scope: str,
    target_slot: str | None = None,
    history_days: int = 3,
) -> GenerationContext:
    """Collect all relevant context required for menu generation using CRUD layer."""
    profile = await crud.get_nutrition_profile(session, user_id)
    profile_read = NutritionProfileRead.model_validate(profile) if profile else None

    ready_dishes = await crud.get_dishes(session, user_id, kind=DishKind.READY, available=True)
    constant_dishes = await crud.get_dishes(session, user_id, kind=DishKind.CONSTANT)

    prod_rows = await crud.get_available_products(session, user_id)
    products = [ProductRead.model_validate(p) for p in prod_rows]

    # Current day menus
    existing_day_menus = await crud.get_day_menus(session, user_id, menu_date)

    # History menus
    history_menus: list[MenuRead] = []
    for day_offset in range(1, history_days + 1):
        prev_date = menu_date - timedelta(days=day_offset)
        prev_day_menus = await crud.get_day_menus(session, user_id, prev_date)
        history_menus.extend(prev_day_menus)

    # Daily corrections
    correction_notes = await crud.get_menu_notes(
        session, user_id, menu_date, kind=MenuNoteKind.CORRECTION
    )
    corrections = [c.text for c in correction_notes]

    # Off-plan entries for today
    off_plan_rows = await crud.get_off_plan_entries(session, user_id, menu_date)
    off_plan_entries = [OffPlanEntryRead.model_validate(o) for o in off_plan_rows]

    return GenerationContext(
        user_id=user_id,
        date=menu_date,
        scope=scope,
        target_slot=target_slot,
        profile=profile_read,
        ready_dishes=ready_dishes,
        constant_dishes=constant_dishes,
        products=products,
        existing_day_menus=existing_day_menus,
        history_menus=history_menus,
        corrections=corrections,
        off_plan_entries=off_plan_entries,
    )
