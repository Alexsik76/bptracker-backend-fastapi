import zoneinfo
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import field_validator
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

    @field_validator("tz")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        try:
            zoneinfo.ZoneInfo(v)
        except Exception as exc:
            raise ValueError(f"Invalid timezone identifier: '{v}'") from exc
        return v


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
