from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse

from auth.deps import get_current_admin_id
from config import Settings, get_settings
from db import SessionDep
from ocr_training import crud, storage
from ocr_training.models import (
    DatasetCreate,
    DatasetDetailResponse,
    ImportSummary,
    JobCreate,
    OcrDataset,
    OcrModel,
    OcrSample,
    OcrTrainingJob,
    SampleLabelsUpdate,
    SampleListResponse,
)

router = APIRouter(
    prefix="/admin/ocr-training",
    tags=["admin-ocr-training"],
    dependencies=[Depends(get_current_admin_id)],
)


@router.post("/samples/import", response_model=ImportSummary)
async def import_samples(
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, int]:
    return await crud.import_samples(session, settings.ocr_photos_dir)


@router.get("/samples", response_model=SampleListResponse)
async def list_samples(
    session: SessionDep,
    source: Annotated[str | None, Query()] = None,
    session_name: Annotated[str | None, Query(alias="session")] = None,
    labeled: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SampleListResponse:
    items, total = await crud.get_samples(
        session,
        source=source,
        session_name=session_name,
        labeled=labeled,
        limit=limit,
        offset=offset,
    )
    return SampleListResponse(
        items=list(items),
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/samples/{id}", response_model=OcrSample)
async def get_sample(
    id: UUID,
    session: SessionDep,
) -> OcrSample:
    sample = await crud.get_sample(session, id)
    if sample is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sample not found")
    return sample


@router.get("/samples/{id}/photo")
async def get_sample_photo(
    id: UUID,
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> FileResponse:
    sample = await crud.get_sample(session, id)
    if sample is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sample not found")

    try:
        path = storage.get_safe_photo_path(settings.ocr_photos_dir, sample.photo_path)
    except storage.PathTraversalError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo file not found")
    return FileResponse(path, media_type="image/jpeg")


@router.put("/samples/{id}/labels", response_model=OcrSample)
async def update_sample_labels(
    id: UUID,
    body: SampleLabelsUpdate,
    session: SessionDep,
) -> OcrSample:
    try:
        return await crud.update_sample_labels(session, id, body.display_box, body.digit_boxes)
    except crud.SampleNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/datasets", response_model=DatasetDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_dataset(
    body: DatasetCreate,
    session: SessionDep,
) -> DatasetDetailResponse:
    try:
        dataset, train_c, val_c, test_c = await crud.create_dataset(session, body)
    except crud.DatasetConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    assert dataset.id is not None
    assert dataset.created_at is not None
    return DatasetDetailResponse(
        id=dataset.id,
        name=dataset.name,
        test_sessions=dataset.test_sessions,
        created_at=dataset.created_at,
        total_samples=train_c + val_c + test_c,
        train_samples=train_c,
        val_samples=val_c,
        test_samples=test_c,
    )


@router.get("/datasets", response_model=list[OcrDataset])
async def list_datasets(
    session: SessionDep,
) -> list[OcrDataset]:
    return list(await crud.list_datasets(session))


@router.get("/datasets/{id}", response_model=DatasetDetailResponse)
async def get_dataset(
    id: UUID,
    session: SessionDep,
) -> DatasetDetailResponse:
    detail = await crud.get_dataset_detail(session, id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
    dataset, total_c, train_c, val_c, test_c = detail
    assert dataset.id is not None
    assert dataset.created_at is not None
    return DatasetDetailResponse(
        id=dataset.id,
        name=dataset.name,
        test_sessions=dataset.test_sessions,
        created_at=dataset.created_at,
        total_samples=total_c,
        train_samples=train_c,
        val_samples=val_c,
        test_samples=test_c,
    )


@router.post("/jobs", response_model=OcrTrainingJob, status_code=status.HTTP_201_CREATED)
async def create_job(
    body: JobCreate,
    session: SessionDep,
) -> OcrTrainingJob:
    try:
        return await crud.create_job(session, body.dataset_id, body.params)
    except crud.DatasetNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/jobs", response_model=list[OcrTrainingJob])
async def list_jobs(
    session: SessionDep,
) -> list[OcrTrainingJob]:
    return list(await crud.list_jobs(session))


@router.get("/jobs/{id}", response_model=OcrTrainingJob)
async def get_job(
    id: UUID,
    session: SessionDep,
) -> OcrTrainingJob:
    job = await crud.get_job(session, id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


@router.get("/models", response_model=list[OcrModel])
async def list_models(
    session: SessionDep,
) -> list[OcrModel]:
    return list(await crud.list_models(session))


@router.post("/models/{id}/make-current", response_model=OcrModel)
async def make_model_current(
    id: UUID,
    session: SessionDep,
) -> OcrModel:
    try:
        return await crud.make_model_current(session, id)
    except crud.ModelNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
