from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
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


@router.post(
    "/csv",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ExportResponse,
)
async def export_csv(
    current_user_id: CurrentUserId,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ExportResponse:
    try:
        email = await export_measurements_to_csv(
            session,
            user_id=current_user_id,
            settings=settings,
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
