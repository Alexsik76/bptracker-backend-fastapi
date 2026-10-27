import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from auth.deps import CurrentUserId
from config import Settings, get_settings
from db import SessionDep
from measurements import crud
from measurements.analyze import AnalyzeResult, GeminiUnavailable, RecognitionFailed, analyze_image
from measurements.models import (
    Measurement,
    MeasurementCreate,
    MeasurementListResponse,
    MeasurementRead,
    MeasurementUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/measurements", tags=["measurements"])


@router.post("", response_model=MeasurementRead, status_code=status.HTTP_201_CREATED)
async def create_measurement(
    data: MeasurementCreate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> Measurement:
    try:
        return await crud.create_measurement(session, data, user_id)
    except crud.MeasurementConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.get("", response_model=MeasurementListResponse)
async def list_measurements(
    session: SessionDep,
    user_id: CurrentUserId,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MeasurementListResponse:
    """List the current user's measurements, newest first, as a paged envelope.

    `date_from` is inclusive, `date_to` is exclusive. Both are optional and
    independent; omitting both returns the user's full history, paged. Values
    are timezone-aware and compared directly against the stored `recorded_at`
    moment — the server does not derive dates from timestamps or apply any
    timezone conversion.
    """
    if date_from is not None and date_to is not None and date_from >= date_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="date_from must be earlier than date_to",
        )
    items, total = await crud.get_measurements(
        session,
        user_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return MeasurementListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{measurement_id}", response_model=MeasurementRead)
async def get_measurement(
    measurement_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> Measurement:
    measurement = await crud.get_measurement(session, measurement_id, user_id)
    if measurement is None:
        raise HTTPException(status_code=404, detail="Measurement not found")
    return measurement


@router.patch("/{measurement_id}", response_model=MeasurementRead)
async def update_measurement(
    measurement_id: UUID,
    data: MeasurementUpdate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> Measurement:
    measurement = await crud.update_measurement(session, measurement_id, data, user_id)
    if measurement is None:
        raise HTTPException(status_code=404, detail="Measurement not found")
    return measurement


@router.delete("/{measurement_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_measurement(
    measurement_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> None:
    deleted = await crud.delete_measurement(session, measurement_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Measurement not found")


@router.post("/analyze", response_model=AnalyzeResult, status_code=status.HTTP_200_OK)
async def analyze_measurement_photo(
    user_id: CurrentUserId,
    settings: Annotated[Settings, Depends(get_settings)],
    image: Annotated[UploadFile | None, File()] = None,
) -> AnalyzeResult:
    # 1. missing file -> 400
    if image is None or not image.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image file is missing")

    # 2. content_type does not start with image/ -> 400
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be an image")

    # Read content to check size
    content = await image.read()

    # 3. size over analyze_max_file_bytes -> 413
    if len(content) > settings.analyze_max_file_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="File exceeds 10 MB"
        )

    try:
        result = await analyze_image(
            image_bytes=content,
            content_type=image.content_type,
            settings=settings,
        )
        logger.info("Successfully analyzed blood pressure photo for user %s", user_id)
        return result
    except GeminiUnavailable as exc:
        logger.error("Gemini service unavailable for user %s", user_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Recognition service unavailable",
        ) from exc
    except RecognitionFailed as exc:
        logger.warning("Gemini recognition failed for user %s", user_id)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Could not recognise values from the image",
        ) from exc
