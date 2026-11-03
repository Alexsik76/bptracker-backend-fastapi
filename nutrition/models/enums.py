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


class MenuOrigin(StrEnum):
    SUGGESTION = "suggestion"
    MANUAL = "manual"
    PINNED = "pinned"


class MenuState(StrEnum):
    SUGGESTION = "suggestion"
    EATEN = "eaten"


class MenuItemOrigin(StrEnum):
    GENERATED = "generated"
    MANUAL = "manual"


class NutritionSource(StrEnum):
    USER = "user"
    MODEL = "model"
    COMPUTED = "computed"


# Backwards compatibility alias
KcalSource = NutritionSource
