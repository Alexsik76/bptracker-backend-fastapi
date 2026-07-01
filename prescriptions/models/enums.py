from enum import StrEnum


class WhenSlot(StrEnum):
    """A time-of-day slot for taking medication."""

    MORNING = "Morning"
    DAY = "Day"
    EVENING = "Evening"


class FreqPeriodUnit(StrEnum):
    """Unit for the frequency period (e.g. '2 times per 1 day')."""

    HOUR = "h"
    DAY = "d"
    WEEK = "wk"


class CourseType(StrEnum):
    """Whether intake is indefinite or a bounded course."""

    ONGOING = "ongoing"
    COURSE = "course"


class DoseUnit(StrEnum):
    """UCUM-subset unit for dose amount. Optional — unit may live in the medicine name."""

    TABLET = "tablet"
    MG = "mg"
    ML = "ml"
    DROP = "drop"
    MCG = "mcg"
    IU = "IU"