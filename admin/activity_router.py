from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from activity.schemas import (
    ErrorEventRead,
    PaginatedErrorEvents,
    PaginatedRequestEvents,
    UsageGroupBy,
    UsageStatItem,
    UserOverviewItem,
)
from activity.service import (
    get_error,
    get_stats_overview,
    get_usage_stats,
    list_errors,
    list_events,
)
from auth.deps import CurrentAdminId
from db import SessionDep

router = APIRouter(prefix="/admin", tags=["admin-activity"])


@router.get("/stats/usage", response_model=list[UsageStatItem])
async def get_usage(
    _: CurrentAdminId,
    session: SessionDep,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
    group_by: Annotated[UsageGroupBy, Query()] = UsageGroupBy.MODULE,
    user_id: Annotated[UUID | None, Query()] = None,
    module: Annotated[str | None, Query()] = None,
) -> list[UsageStatItem]:
    return await get_usage_stats(
        session=session,
        date_from=date_from,
        date_to=date_to,
        group_by=group_by,
        user_id=user_id,
        module=module,
    )


@router.get("/stats/overview", response_model=list[UserOverviewItem])
async def get_overview(
    _: CurrentAdminId,
    session: SessionDep,
) -> list[UserOverviewItem]:
    return await get_stats_overview(session=session)


@router.get("/events", response_model=PaginatedRequestEvents)
async def get_events(
    _: CurrentAdminId,
    session: SessionDep,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
    user_id: Annotated[UUID | None, Query()] = None,
    module: Annotated[str | None, Query()] = None,
    client: Annotated[str | None, Query()] = None,
    status_min: Annotated[int | None, Query(ge=100, le=599)] = None,
    status_max: Annotated[int | None, Query(ge=100, le=599)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedRequestEvents:
    return await list_events(
        session=session,
        date_from=date_from,
        date_to=date_to,
        user_id=user_id,
        module=module,
        client=client,
        status_min=status_min,
        status_max=status_max,
        limit=limit,
        offset=offset,
    )


@router.get("/errors", response_model=PaginatedErrorEvents)
async def get_errors(
    _: CurrentAdminId,
    session: SessionDep,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedErrorEvents:
    return await list_errors(
        session=session,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


@router.get("/errors/{error_id}", response_model=ErrorEventRead)
async def get_error_by_id(
    error_id: UUID,
    _: CurrentAdminId,
    session: SessionDep,
) -> ErrorEventRead:
    error = await get_error(session=session, error_id=error_id)
    if error is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Error event not found",
        )
    return error
