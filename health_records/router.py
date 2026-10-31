from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from auth.deps import CurrentUserId
from db import SessionDep
from health_records import crud
from health_records.models import (
    HealthRecordBatchRequest,
    HealthRecordBatchResponse,
    HealthRecordListResponse,
    HealthRecordRead,
)

router = APIRouter(prefix="/health-records", tags=["health-records"])


@router.post("/batch", response_model=HealthRecordBatchResponse)
async def batch_health_records(
    request: HealthRecordBatchRequest,
    session: SessionDep,
    user_id: CurrentUserId,
) -> HealthRecordBatchResponse:
    created, updated, deleted = await crud.batch_sync(
        session,
        user_id,
        upserts=request.upserts,
        deleted_ids=request.deleted_ids,
    )
    return HealthRecordBatchResponse(
        created=created,
        updated=updated,
        deleted=deleted,
    )


@router.get("", response_model=HealthRecordListResponse)
async def list_health_records(
    session: SessionDep,
    user_id: CurrentUserId,
    record_type: Annotated[str | None, Query()] = None,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> HealthRecordListResponse:
    """List the current user's health records, newest start_time first, as a paged envelope.

    `date_from` is inclusive, `date_to` is exclusive on `start_time`.
    """
    if date_from is not None and date_to is not None and date_from >= date_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="date_from must be before date_to",
        )
    items, total = await crud.get_health_records(
        session,
        user_id,
        record_type=record_type,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return HealthRecordListResponse(
        items=[HealthRecordRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )
