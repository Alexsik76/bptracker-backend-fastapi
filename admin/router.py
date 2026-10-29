from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status

from admin.schemas import (
    CreateRowRequest,
    DeleteRowRequest,
    RowsResponse,
    TableMetadata,
    UpdateRowRequest,
)
from admin.service import (
    AdminServiceError,
    CoercionError,
    ConflictError,
    ForbiddenOperationError,
    InvalidColumnError,
    RowNotFoundError,
    TableNotFoundError,
    create_row,
    delete_row,
    get_tables_metadata,
    list_rows,
    update_row,
)
from auth.deps import CurrentAdminId
from db import SessionDep

router = APIRouter(prefix="/admin", tags=["admin"])


def handle_service_error(exc: AdminServiceError) -> HTTPException:
    if isinstance(exc, (TableNotFoundError, RowNotFoundError)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    elif isinstance(exc, ForbiddenOperationError):
        return HTTPException(status_code=status.HTTP_405_METHOD_NOT_ALLOWED, detail=str(exc))
    elif isinstance(exc, (InvalidColumnError, CoercionError)):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    elif isinstance(exc, ConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message)
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/ping")
async def ping(_: CurrentAdminId) -> dict[str, str]:
    return {"status": "ok"}


@router.get("/tables", response_model=list[TableMetadata])
async def list_tables(_: CurrentAdminId) -> list[dict[str, Any]]:
    try:
        return get_tables_metadata()
    except AdminServiceError as exc:
        raise handle_service_error(exc) from exc


@router.get("/tables/{name}/rows", response_model=RowsResponse)
async def get_table_rows(
    name: str,
    _: CurrentAdminId,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    order_by: Annotated[str | None, Query()] = None,
    desc: Annotated[bool, Query()] = False,
    search: Annotated[str | None, Query()] = None,
    filter: Annotated[list[str] | None, Query()] = None,
) -> dict[str, Any]:
    try:
        return await list_rows(
            table_name=name,
            session=session,
            limit=limit,
            offset=offset,
            order_by=order_by,
            desc=desc,
            search=search,
            filters=filter or [],
        )
    except AdminServiceError as exc:
        raise handle_service_error(exc) from exc


@router.post("/tables/{name}/rows", status_code=status.HTTP_201_CREATED)
async def create_table_row(
    name: str,
    body: CreateRowRequest,
    _: CurrentAdminId,
    session: SessionDep,
) -> dict[str, Any]:
    try:
        return await create_row(
            table_name=name,
            values=body.values,
            session=session,
        )
    except AdminServiceError as exc:
        raise handle_service_error(exc) from exc


@router.patch("/tables/{name}/rows", status_code=status.HTTP_200_OK)
async def update_table_row(
    name: str,
    body: UpdateRowRequest,
    _: CurrentAdminId,
    session: SessionDep,
) -> dict[str, Any]:
    try:
        return await update_row(
            table_name=name,
            pk=body.pk,
            values=body.values,
            session=session,
        )
    except AdminServiceError as exc:
        raise handle_service_error(exc) from exc


@router.post("/tables/{name}/rows/delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete_table_row(
    name: str,
    body: DeleteRowRequest,
    _: CurrentAdminId,
    session: SessionDep,
) -> None:
    try:
        await delete_row(
            table_name=name,
            pk=body.pk,
            session=session,
        )
    except AdminServiceError as exc:
        raise handle_service_error(exc) from exc
