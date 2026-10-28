from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from uuid import UUID

from sqlmodel.ext.asyncio.session import AsyncSession

from nutrition import crud
from nutrition.models import (
    DishRead,
    MealType,
    MenuNoteKind,
    MenuRead,
    NutritionProfileRead,
    OffPlanEntryRead,
    ProductRead,
    is_meal_generatable,
)


@dataclass
class GenerationContext:
    user_id: UUID
    date: date
    scope: str
    target_meal_type: MealType | None
    profile: NutritionProfileRead | None
    dishes: list[DishRead]
    products: list[ProductRead]
    existing_day_menus: list[MenuRead]
    history_menus: list[MenuRead]
    corrections: list[str] = field(default_factory=list)
    off_plan_entries: list[OffPlanEntryRead] = field(default_factory=list)

    @property
    def dishes_map(self) -> dict[UUID, DishRead]:
        return {d.id: d for d in self.dishes}

    @property
    def products_map(self) -> dict[UUID, ProductRead]:
        return {p.id: p for p in self.products}


async def build_generation_context(
    session: AsyncSession,
    user_id: UUID,
    menu_date: date,
    scope: str,
    target_meal_type: MealType | None = None,
    history_days: int = 3,
    now: datetime | None = None,
) -> GenerationContext:
    """Collect all relevant context required for menu generation using CRUD layer."""
    profile = await crud.get_nutrition_profile(session, user_id)
    profile_read = NutritionProfileRead.model_validate(profile) if profile else None

    # Current day menus
    existing_day_menus = await crud.get_day_menus(session, user_id, menu_date, now=now)

    # Fetch available dishes (remaining_g > 0)
    all_dishes = await crud.get_dishes(session, user_id, available=True)

    # Deduct portions already planned in uneaten locked meals
    planned_portions: dict[UUID, int] = {}
    for menu in existing_day_menus:
        is_locked = not is_meal_generatable(menu) or (
            target_meal_type and menu.meal_type != target_meal_type
        )
        if is_locked and menu.eaten_at is None:
            for it in menu.items:
                if it.dish_id and it.portion_g:
                    planned_portions[it.dish_id] = (
                        planned_portions.get(it.dish_id, 0) + it.portion_g
                    )

    dishes: list[DishRead] = []
    for d in all_dishes:
        planned = planned_portions.get(d.id, 0)
        eff_remaining = d.remaining_g - planned
        if eff_remaining > 0:
            d_copy = d.model_copy(update={"remaining_g": eff_remaining})
            dishes.append(d_copy)

    prod_rows = await crud.get_available_products(session, user_id)
    products = [ProductRead.model_validate(p) for p in prod_rows]

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
        target_meal_type=target_meal_type,
        profile=profile_read,
        dishes=dishes,
        products=products,
        existing_day_menus=existing_day_menus,
        history_menus=history_menus,
        corrections=corrections,
        off_plan_entries=off_plan_entries,
    )
