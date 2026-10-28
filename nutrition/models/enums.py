from enum import StrEnum


class MealType(StrEnum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    MORNING_SNACK = "morning_snack"
    AFTERNOON_SNACK = "afternoon_snack"
    EVENING_SNACK = "evening_snack"


MEAL_TYPE_LABELS: dict[MealType, str] = {
    MealType.BREAKFAST: "Сніданок",
    MealType.LUNCH: "Обід",
    MealType.DINNER: "Вечеря",
    MealType.MORNING_SNACK: "Ранковий перекус",
    MealType.AFTERNOON_SNACK: "Полуденок",
    MealType.EVENING_SNACK: "Вечірній перекус",
}


class Sex(StrEnum):
    MALE = "male"
    FEMALE = "female"


class ProductAvailability(StrEnum):
    IN_STOCK = "in_stock"
    LOW = "low"
    OUT = "out"


class ProductPreference(StrEnum):
    PREFERRED = "preferred"
    NEUTRAL = "neutral"
    AVOID = "avoid"


class MenuItemOrigin(StrEnum):
    GENERATED = "generated"
    MANUAL = "manual"


class NutritionSource(StrEnum):
    USER = "user"
    MODEL = "model"
    COMPUTED = "computed"


# Backwards compatibility alias
KcalSource = NutritionSource


class MenuStatus(StrEnum):
    PLANNED = "planned"
    INCOMPLETE = "incomplete"
    EATEN = "eaten"
    MISSED = "missed"


class GenerationScope(StrEnum):
    DAY = "day"
    SLOT = "slot"
    UNMARKED = "unmarked"


class MenuNoteKind(StrEnum):
    INFO = "info"
    CORRECTION = "correction"
