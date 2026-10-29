from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app_settings import ai_service, service
from app_settings.registry import SettingValueError
from app_settings.schemas import (
    AiTestRequest,
    AiTestResult,
    GeminiModelInfo,
    SettingRead,
    SettingUpdate,
)
from auth.deps import CurrentAdminId
from config import Settings, get_settings
from db import SessionDep

router = APIRouter(prefix="/admin/settings", tags=["admin-settings"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("", response_model=list[SettingRead])
async def list_settings(
    _: CurrentAdminId, session: SessionDep, settings: SettingsDep
) -> list[SettingRead]:
    return await service.list_settings(session, settings)


@router.get("/ai/models", response_model=list[GeminiModelInfo])
async def list_ai_models(_: CurrentAdminId, settings: SettingsDep) -> list[GeminiModelInfo]:
    try:
        return await ai_service.list_generate_models(settings)
    except ai_service.GeminiModelsUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/ai/test", response_model=AiTestResult)
async def test_ai(body: AiTestRequest, _: CurrentAdminId, settings: SettingsDep) -> AiTestResult:
    return await ai_service.run_connection_test(settings, body.model)


@router.put("/{key}", response_model=SettingRead)
async def update_setting(
    key: str,
    body: SettingUpdate,
    admin_id: CurrentAdminId,
    session: SessionDep,
    settings: SettingsDep,
) -> SettingRead:
    try:
        return await service.set_setting(session, settings, key, body.value, admin_id)
    except service.UnknownSettingError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SettingValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_setting(key: str, _: CurrentAdminId, session: SessionDep) -> Response:
    try:
        await service.reset_setting(session, key)
    except service.UnknownSettingError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
