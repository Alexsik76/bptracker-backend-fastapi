import zoneinfo
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import field_validator, model_validator
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from auth.deps import CurrentUserId
from config import Settings, get_settings
from db import get_session
from export.models import ExportResponse
from export.service import (
    ExportCooldownActive,
    UserNotFound,
    export_measurements_to_csv,
)

router = APIRouter(prefix="/export", tags=["export"])


class ExportRequest(SQLModel):
    tz: str
    date_from: date | None = None
    date_to: date | None = None

    @field_validator("tz")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        try:
            zoneinfo.ZoneInfo(v)
        except Exception as exc:
            raise ValueError(f"Invalid timezone identifier: '{v}'") from exc
        return v

    @model_validator(mode="after")
    def validate_date_range(self) -> "ExportRequest":
        if self.date_from is not None and self.date_to is not None:
            if self.date_from > self.date_to:
                raise ValueError("date_from cannot be after date_to")
        return self


@router.post(
    "/csv",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ExportResponse,
)
async def export_csv(
    data: ExportRequest,
    current_user_id: CurrentUserId,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ExportResponse:
    try:
        email = await export_measurements_to_csv(
            session,
            user_id=current_user_id,
            settings=settings,
            tz=data.tz,
            date_from=data.date_from,
            date_to=data.date_to,
        )
    except UserNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        ) from exc
    except ExportCooldownActive as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Export already requested recently",
        ) from exc
    return ExportResponse(message="Export is queued", email=email)
