from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class SettingRead(BaseModel):
    key: str
    label: str
    type: str
    constraints: dict[str, Any]
    value: Any
    source: Literal["db", "env"]
    env_value: Any
    updated_at: datetime | None
    updated_by: UUID | None


class SettingUpdate(BaseModel):
    value: Any


class GeminiModelInfo(BaseModel):
    name: str
    display_name: str
    input_token_limit: int | None
    output_token_limit: int | None


class AiTestRequest(BaseModel):
    model: str | None = None


class AiTestResult(BaseModel):
    ok: bool
    model: str
    latency_ms: int
    error: str | None
