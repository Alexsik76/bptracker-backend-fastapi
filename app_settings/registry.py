from dataclasses import dataclass
from typing import Any, Literal

SettingType = Literal["str", "int", "bool"]


class SettingValueError(ValueError):
    """Raised when a value violates the type or constraints of a registered setting."""


@dataclass(frozen=True)
class SettingDef:
    key: str
    label: str
    type: SettingType
    # Name of the config.Settings attribute this setting overrides.
    env_attr: str
    min: int | None = None
    max: int | None = None

    @property
    def constraints(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.min is not None:
            result["min"] = self.min
        if self.max is not None:
            result["max"] = self.max
        if self.type == "str":
            result["min_length"] = 1
        return result

    def validate(self, value: Any) -> Any:
        if self.type == "bool":
            if not isinstance(value, bool):
                raise SettingValueError(f"{self.key} must be a boolean")
            return value
        if self.type == "int":
            # bool is a subclass of int; reject it explicitly.
            if isinstance(value, bool) or not isinstance(value, int):
                raise SettingValueError(f"{self.key} must be an integer")
            if self.min is not None and value < self.min:
                raise SettingValueError(f"{self.key} must be >= {self.min}")
            if self.max is not None and value > self.max:
                raise SettingValueError(f"{self.key} must be <= {self.max}")
            return value
        if not isinstance(value, str) or not value.strip():
            raise SettingValueError(f"{self.key} must be a non-empty string")
        return value.strip()


_DEFINITIONS = (
    SettingDef(
        key="ai.gemini.analyze_model",
        label="Gemini model for blood pressure photo analysis",
        type="str",
        env_attr="gemini_model",
    ),
    SettingDef(
        key="ai.gemini.menu_model",
        label="Gemini model for nutrition menus",
        type="str",
        env_attr="gemini_menu_model",
    ),
    SettingDef(
        key="ai.gemini.timeout_seconds",
        label="Gemini request timeout (seconds)",
        type="int",
        env_attr="gemini_timeout_seconds",
        min=5,
        max=120,
    ),
    SettingDef(
        key="ai.llm_exchange_log",
        label="Log full LLM prompt/response exchanges to stdout",
        type="bool",
        env_attr="llm_exchange_log",
    ),
    SettingDef(
        key="nutrition.menu_history_days",
        label="Days of menu history passed to the generator",
        type="int",
        env_attr="menu_history_days",
        min=0,
        max=30,
    ),
)

REGISTRY: dict[str, SettingDef] = {d.key: d for d in _DEFINITIONS}
