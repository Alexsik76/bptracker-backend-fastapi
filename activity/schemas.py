from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlmodel import SQLModel


class UsageGroupBy(StrEnum):
    USER = "user"
    MODULE = "module"
    CLIENT = "client"
    DAY = "day"
    ROUTE = "route"


class UsageStatItem(SQLModel):
    key: str
    label: str | None = None
    requests: int
    errors: int
    client_errors: int
    avg_ms: float
    p95_ms: float


class UserOverviewItem(SQLModel):
    email: str
    display_name: str | None = None
    created_at: datetime
    last_request_at: datetime | None = None
    requests_30d: int
    measurements_total: int
    measurements_30d: int
    prescriptions_active: int
    intake_reports_30d: int
    menu_generations_30d: int
    webauthn_credentials: int
    active_sessions: int


class RequestEventRead(SQLModel):
    id: UUID
    occurred_at: datetime
    user_id: UUID | None = None
    client: str
    module: str
    method: str
    route: str
    status_code: int
    duration_ms: int


class PaginatedRequestEvents(SQLModel):
    items: list[RequestEventRead]
    total: int
    limit: int
    offset: int


class ErrorEventRead(SQLModel):
    id: UUID
    occurred_at: datetime
    user_id: UUID | None = None
    method: str
    route: str
    exception_type: str
    message: str
    traceback: str


class PaginatedErrorEvents(SQLModel):
    items: list[ErrorEventRead]
    total: int
    limit: int
    offset: int
