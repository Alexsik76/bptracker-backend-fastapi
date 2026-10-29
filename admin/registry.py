from dataclasses import dataclass, field

from sqlmodel import SQLModel

from auth.models import Session
from auth.webauthn.models import WebAuthnCredential
from email_infra.models import EmailOutbox
from export.models import ExportState
from measurements.models import Measurement
from nutrition.models import (
    Dish,
    DishComponent,
    FrequentDish,
    FrequentDishComponent,
    GenerationLog,
    Menu,
    MenuDay,
    MenuItem,
    MenuNote,
    NutritionProfile,
    OffPlanEntry,
    PinnedMeal,
    PinnedMealItem,
    Product,
    Recipe,
    RecipeProduct,
)
from prescriptions.models import MedicationItem, Prescription
from reminders.models import IntakeReport, ReminderConfig
from users.models import User


@dataclass(frozen=True)
class TableConfig:
    model: type[SQLModel]
    label: str
    can_create: bool = False
    can_update: bool = False
    can_delete: bool = False
    hidden_columns: frozenset[str] = field(default_factory=frozenset)
    read_only_columns: frozenset[str] = field(default_factory=frozenset)

    def is_column_hidden(self, column_name: str) -> bool:
        name_lower = column_name.lower()
        return (
            column_name in self.hidden_columns
            or "hash" in name_lower
            or "secret" in name_lower
            or "public_key" in name_lower
        )

    def get_effective_hidden_columns(self) -> frozenset[str]:
        hidden = set(self.hidden_columns)
        for col in self.model.__table__.columns:
            name_lower = col.name.lower()
            if "hash" in name_lower or "secret" in name_lower or "public_key" in name_lower:
                hidden.add(col.name)
        return frozenset(hidden)

    def get_effective_read_only_columns(self) -> frozenset[str]:
        actual_cols = {col.name for col in self.model.__table__.columns}
        ro = {col_name for col_name in self.read_only_columns if col_name in actual_cols}
        for name in ("id", "created_at", "updated_at"):
            if name in actual_cols:
                ro.add(name)
        return frozenset(ro)


TABLE_REGISTRY: dict[str, TableConfig] = {
    # users: create/update/delete; id, created_at derived read-only
    User.__tablename__: TableConfig(
        model=User,
        label="Користувачі",
        can_create=True,
        can_update=True,
        can_delete=True,
    ),
    # measurements: full CRUD; id derived read-only; recorded_at is editable (offline-first)
    Measurement.__tablename__: TableConfig(
        model=Measurement,
        label="Вимірювання",
        can_create=True,
        can_update=True,
        can_delete=True,
    ),
    Prescription.__tablename__: TableConfig(
        model=Prescription,
        label="Призначення",
        can_create=True,
        can_update=True,
        can_delete=True,
    ),
    MedicationItem.__tablename__: TableConfig(
        model=MedicationItem,
        label="Медикаменти",
        can_create=True,
        can_update=True,
        can_delete=True,
    ),
    ReminderConfig.__tablename__: TableConfig(
        model=ReminderConfig,
        label="Налаштування нагадувань",
        can_create=True,
        can_update=True,
        can_delete=True,
    ),
    # intake_reports: id derived; recorded_at is server timestamp (read-only)
    IntakeReport.__tablename__: TableConfig(
        model=IntakeReport,
        label="Звіти про прийом",
        can_create=True,
        can_update=True,
        can_delete=True,
        read_only_columns=frozenset({"recorded_at"}),
    ),
    # all nutrition tables (nutrition/models): full CRUD, except generation log: read + delete only
    Dish.__tablename__: TableConfig(
        model=Dish,
        label="Страви",
        can_create=True,
        can_update=True,
        can_delete=True,
    ),
    DishComponent.__tablename__: TableConfig(
        model=DishComponent,
        label="Компоненти страв",
        can_create=True,
        can_update=True,
        can_delete=True,
    ),
    FrequentDish.__tablename__: TableConfig(
        model=FrequentDish,
        label="Часті страви",
        can_create=True,
        can_update=True,
        can_delete=True,
    ),
    FrequentDishComponent.__tablename__: TableConfig(
        model=FrequentDishComponent,
        label="Компоненти частих страв",
        can_create=True,
        can_update=True,
        can_delete=True,
    ),
    GenerationLog.__tablename__: TableConfig(
        model=GenerationLog,
        label="Журнал генерацій",
        can_create=False,
        can_update=False,
        can_delete=True,
    ),
    Menu.__tablename__: TableConfig(
        model=Menu,
        label="Меню",
        can_create=True,
        can_update=True,
        can_delete=True,
    ),
    MenuDay.__tablename__: TableConfig(
        model=MenuDay,
        label="Дні меню",
        can_create=True,
        can_update=True,
        can_delete=True,
    ),
    MenuItem.__tablename__: TableConfig(
        model=MenuItem,
        label="Елементи меню",
        can_create=True,
        can_update=True,
        can_delete=True,
    ),
    MenuNote.__tablename__: TableConfig(
        model=MenuNote,
        label="Нотатки до меню",
        can_create=True,
        can_update=True,
        can_delete=True,
    ),
    OffPlanEntry.__tablename__: TableConfig(
        model=OffPlanEntry,
        label="Позапланові прийоми",
        can_create=True,
        can_update=True,
        can_delete=True,
    ),
    PinnedMeal.__tablename__: TableConfig(
        model=PinnedMeal,
        label="Закріплені прийоми",
        can_create=True,
        can_update=True,
        can_delete=True,
    ),
    PinnedMealItem.__tablename__: TableConfig(
        model=PinnedMealItem,
        label="Елементи закріплених прийомів",
        can_create=True,
        can_update=True,
        can_delete=True,
    ),
    Product.__tablename__: TableConfig(
        model=Product,
        label="Продукти",
        can_create=True,
        can_update=True,
        can_delete=True,
    ),
    NutritionProfile.__tablename__: TableConfig(
        model=NutritionProfile,
        label="Профілі харчування",
        can_create=True,
        can_update=True,
        can_delete=True,
    ),
    Recipe.__tablename__: TableConfig(
        model=Recipe,
        label="Рецепти",
        can_create=True,
        can_update=True,
        can_delete=True,
    ),
    RecipeProduct.__tablename__: TableConfig(
        model=RecipeProduct,
        label="Інгредієнти рецептів",
        can_create=True,
        can_update=True,
        can_delete=True,
    ),
    # auth sessions and WebAuthn credentials: read + delete only;
    # token hashes and public keys hidden
    Session.__tablename__: TableConfig(
        model=Session,
        label="Сесії",
        can_create=False,
        can_update=False,
        can_delete=True,
        hidden_columns=frozenset({"token_hash"}),
    ),
    WebAuthnCredential.__tablename__: TableConfig(
        model=WebAuthnCredential,
        label="Облікові дані WebAuthn",
        can_create=False,
        can_update=False,
        can_delete=True,
        hidden_columns=frozenset({"public_key"}),
    ),
    # email outbox and export state: read only
    EmailOutbox.__tablename__: TableConfig(
        model=EmailOutbox,
        label="Вихідні листи",
        can_create=False,
        can_update=False,
        can_delete=False,
    ),
    ExportState.__tablename__: TableConfig(
        model=ExportState,
        label="Стан експорту",
        can_create=False,
        can_update=False,
        can_delete=False,
    ),
}
