from enum import StrEnum


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


class DishKind(StrEnum):
    READY = "ready"
    CONSTANT = "constant"


class MenuItemOrigin(StrEnum):
    GENERATED = "generated"
    MANUAL = "manual"


class KcalSource(StrEnum):
    USER = "user"
    MODEL = "model"


class MenuStatus(StrEnum):
    PLANNED = "planned"
    INCOMPLETE = "incomplete"
    EATEN = "eaten"


class GenerationScope(StrEnum):
    DAY = "day"
    SLOT = "slot"
    UNMARKED = "unmarked"


class MenuNoteKind(StrEnum):
    INFO = "info"
    CORRECTION = "correction"
