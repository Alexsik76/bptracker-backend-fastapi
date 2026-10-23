from datetime import UTC, date, datetime
from uuid import UUID

import pydantic
import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from nutrition.models import (
    Dish,
    DishComponent,
    DishKind,
    MealSchedule,
    MenuNote,
    MenuNoteKind,
    NutritionProfile,
    OffPlanEntry,
    OffPlanEntryCreate,
    Product,
    ProductAvailability,
    ProductPreference,
    Sex,
)


@pytest.mark.asyncio
async def test_nutrition_profile_persistence(session: AsyncSession, test_user_id: UUID):
    profile = NutritionProfile(
        user_id=test_user_id,
        sex=Sex.MALE,
        birth_year=1976,
        height_cm=200,
        weight_kg=118.0,
        target_weight_kg=110.0,
        pace_kg_per_week=0.75,
        restrictions="Low salt",
        dislikes="Liver",
        meals=[
            MealSchedule(name="Сніданок", time="08:00"),
            MealSchedule(name="Обід", time="13:00"),
            MealSchedule(name="Вечеря", time="19:00"),
        ],
    )
    session.add(profile)
    await session.commit()
    await session.refresh(profile)

    stmt = select(NutritionProfile).where(NutritionProfile.user_id == test_user_id)
    saved = (await session.exec(stmt)).first()
    assert saved is not None
    assert saved.sex == Sex.MALE
    assert saved.birth_year == 1976
    assert saved.weight_kg == 118.0
    assert len(saved.meals) == 3
    assert saved.meals[0].name == "Сніданок"
    assert saved.updated_at is not None


@pytest.mark.asyncio
async def test_product_persistence(session: AsyncSession, test_user_id: UUID):
    product = Product(
        user_id=test_user_id,
        name="Вівсянка",
        availability=ProductAvailability.IN_STOCK,
        preference=ProductPreference.PREFERRED,
        eat_raw=False,
        daily_limit_g=100,
    )
    session.add(product)
    await session.commit()
    await session.refresh(product)

    assert product.id is not None
    assert product.created_at is not None
    assert product.updated_at is not None

    stmt = select(Product).where(Product.user_id == test_user_id)
    saved = (await session.exec(stmt)).first()
    assert saved is not None
    assert saved.name == "Вівсянка"
    assert saved.availability == ProductAvailability.IN_STOCK
    assert saved.preference == ProductPreference.PREFERRED
    assert saved.eat_raw is False
    assert saved.daily_limit_g == 100


@pytest.mark.asyncio
async def test_dish_and_components_persistence(session: AsyncSession, test_user_id: UUID):
    dish = Dish(
        user_id=test_user_id,
        name="Вівсянка з яблуком",
        kind=DishKind.CONSTANT,
        cooked_on=date.today(),
        fixed_slot="Сніданок",
        notes="Постійна ранкова страва",
    )
    session.add(dish)
    await session.commit()
    await session.refresh(dish)

    assert dish.id is not None

    comp1 = DishComponent(
        dish_id=dish.id,
        name="Вівсяні пластівці",
        raw_weight_g=45,
    )
    comp2 = DishComponent(
        dish_id=dish.id,
        name="Яблуко",
        raw_weight_g=100,
    )
    session.add_all([comp1, comp2])
    await session.commit()

    stmt = select(DishComponent).where(DishComponent.dish_id == dish.id)
    comps = (await session.exec(stmt)).all()
    assert len(comps) == 2
    names = {c.name for c in comps}
    assert names == {"Вівсяні пластівці", "Яблуко"}


@pytest.mark.asyncio
async def test_menu_note_persistence(session: AsyncSession, test_user_id: UUID):
    today = date.today()
    note = MenuNote(
        user_id=test_user_id,
        date=today,
        kind=MenuNoteKind.INFO,
        text="Чи можна замінити гречку на бурий рис?",
        answer="Так, бурий рис має близький глікемічний індекс.",
    )
    session.add(note)
    await session.commit()
    await session.refresh(note)

    assert note.id is not None
    assert note.created_at is not None

    stmt = select(MenuNote).where(MenuNote.user_id == test_user_id)
    saved = (await session.exec(stmt)).first()
    assert saved is not None
    assert saved.kind == MenuNoteKind.INFO
    assert saved.date == today
    assert saved.text == "Чи можна замінити гречку на бурий рис?"
    assert saved.answer == "Так, бурий рис має близький глікемічний індекс."


@pytest.mark.asyncio
async def test_off_plan_entry_persistence(session: AsyncSession, test_user_id: UUID):
    today = date.today()
    now_dt = datetime.now(UTC)
    entry = OffPlanEntry(
        user_id=test_user_id,
        date=today,
        at=now_dt,
        text="Яблуко зелене 150г",
        kcal_est=78,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)

    assert entry.id is not None

    stmt = select(OffPlanEntry).where(OffPlanEntry.user_id == test_user_id)
    saved = (await session.exec(stmt)).first()
    assert saved is not None
    assert saved.date == today
    assert saved.text == "Яблуко зелене 150г"
    assert saved.kcal_est == 78
    assert saved.at == now_dt


def test_off_plan_entry_create_requires_date():
    with pytest.raises(pydantic.ValidationError):
        OffPlanEntryCreate(
            at=datetime.now(UTC),
            text="Яблуко зелене 150г",
        )


