import hmac
import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse

from config import Settings, get_settings
from db import SessionDep
from ocr_training import crud, storage
from ocr_training.models import (
    JobFailRequest,
    OcrModel,
    OcrTrainingJob,
    SampleManifestItem,
)


def verify_trainer_token(
    authorization: Annotated[str | None, Header()] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
) -> None:
    if not settings.ocr_trainer_token or not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = parts[1]
    if not hmac.compare_digest(token, settings.ocr_trainer_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )


router = APIRouter(
    prefix="/ocr-trainer",
    tags=["ocr-trainer"],
    dependencies=[Depends(verify_trainer_token)],
)


@router.post("/jobs/claim", response_model=None)
async def claim_job(
    session: SessionDep,
) -> Response | dict[str, Any]:
    claimed = await crud.claim_oldest_job(session)
    if claimed is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    job, manifest = claimed
    items = [SampleManifestItem.model_validate(m) for m in manifest]
    return {
        "job": job,
        "dataset_id": job.dataset_id,
        "manifest": items,
        "samples": items,
    }


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


@router.post("/jobs/{id}/result", response_model=OcrModel)
async def submit_job_result(
    id: UUID,
    report: Annotated[str, Form()],
    display_model: Annotated[UploadFile, File()],
    digit_model: Annotated[UploadFile, File()],
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> OcrModel:
    try:
        report_dict = json.loads(report)
        if not isinstance(report_dict, dict):
            raise ValueError("Report must be a JSON object")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON report: {exc}",
        ) from exc

    display_bytes = await display_model.read()
    digit_bytes = await digit_model.read()

    try:
        return await crud.complete_job_with_result(
            session=session,
            job_id=id,
            report_dict=report_dict,
            display_model_bytes=display_bytes,
            digit_model_bytes=digit_bytes,
            ocr_models_dir=settings.ocr_models_dir,
        )
    except crud.JobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except crud.JobStateConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/jobs/{id}/fail", response_model=OcrTrainingJob)
async def fail_job(
    id: UUID,
    body: JobFailRequest,
    session: SessionDep,
) -> OcrTrainingJob:
    try:
        return await crud.fail_job(session=session, job_id=id, error=body.error)
    except crud.JobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except crud.JobStateConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
