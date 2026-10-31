import hashlib
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, text, update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ocr_training import storage
from ocr_training.models import (
    DatasetCreate,
    OcrDataset,
    OcrDatasetSample,
    OcrModel,
    OcrSample,
    OcrTrainingJob,
)

logger = logging.getLogger(__name__)


class OcrTrainingError(Exception):
    """Base exception for OCR training domain errors."""


class SampleNotFoundError(OcrTrainingError):
    """Raised when an OCR sample is not found."""


class DatasetNotFoundError(OcrTrainingError):
    """Raised when an OCR dataset is not found."""


class DatasetConflictError(OcrTrainingError):
    """Raised when an OCR dataset with the same name already exists."""


class JobNotFoundError(OcrTrainingError):
    """Raised when a training job is not found."""


class JobStateConflictError(OcrTrainingError):
    """Raised when a job operation is invalid due to its current status."""


class ModelNotFoundError(OcrTrainingError):
    """Raised when an OCR model is not found."""


async def import_samples(session: AsyncSession, ocr_photos_dir: Path) -> dict[str, int]:
    valid_samples, skipped_files_count = storage.scan_photo_storage(ocr_photos_dir)

    created = 0
    updated = 0
    skipped = skipped_files_count

    for sample_data in valid_samples:
        rel_path = sample_data["photo_path"]
        stmt = select(OcrSample).where(OcrSample.photo_path == rel_path)
        existing = (await session.exec(stmt)).first()

        if existing is None:
            new_sample = OcrSample(
                photo_path=rel_path,
                ref_sys=sample_data["ref_sys"],
                ref_dia=sample_data["ref_dia"],
                ref_pul=sample_data["ref_pul"],
                taken_at=sample_data["taken_at"],
                device_model=sample_data["device_model"],
                source=sample_data["source"],
                session=sample_data["session"],
                corrected_by_user=sample_data["corrected_by_user"],
                meta=sample_data["meta"],
            )
            session.add(new_sample)
            created += 1
        else:
            changed = (
                existing.ref_sys != sample_data["ref_sys"]
                or existing.ref_dia != sample_data["ref_dia"]
                or existing.ref_pul != sample_data["ref_pul"]
                or existing.taken_at != sample_data["taken_at"]
                or existing.device_model != sample_data["device_model"]
                or existing.source != sample_data["source"]
                or existing.session != sample_data["session"]
                or existing.corrected_by_user != sample_data["corrected_by_user"]
                or existing.meta != sample_data["meta"]
            )
            if changed:
                existing.ref_sys = sample_data["ref_sys"]
                existing.ref_dia = sample_data["ref_dia"]
                existing.ref_pul = sample_data["ref_pul"]
                existing.taken_at = sample_data["taken_at"]
                existing.device_model = sample_data["device_model"]
                existing.source = sample_data["source"]
                existing.session = sample_data["session"]
                existing.corrected_by_user = sample_data["corrected_by_user"]
                existing.meta = sample_data["meta"]
                existing.updated_at = datetime.now(UTC)
                session.add(existing)
                updated += 1
            else:
                skipped += 1

    await session.commit()
    return {"created": created, "updated": updated, "skipped": skipped}


async def get_samples(
    session: AsyncSession,
    *,
    source: str | None = None,
    session_name: str | None = None,
    labeled: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[Sequence[OcrSample], int]:
    conditions = []
    if source is not None:
        conditions.append(OcrSample.source == source)
    if session_name is not None:
        conditions.append(OcrSample.session == session_name)
    if labeled is True:
        conditions.append(
            and_(
                OcrSample.display_box.is_not(None),
                OcrSample.display_box != text("'null'::jsonb"),
            )
        )
    elif labeled is False:
        conditions.append(
            or_(
                OcrSample.display_box.is_(None),
                OcrSample.display_box == text("'null'::jsonb"),
            )
        )

    count_stmt = select(func.count()).select_from(OcrSample)
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total = (await session.exec(count_stmt)).one()

    stmt = select(OcrSample)
    if conditions:
        stmt = stmt.where(*conditions)
    stmt = stmt.order_by(OcrSample.created_at.desc()).limit(limit).offset(offset)
    items = (await session.exec(stmt)).all()
    return items, total


async def get_sample(session: AsyncSession, sample_id: UUID) -> OcrSample | None:
    return await session.get(OcrSample, sample_id)


async def update_sample_labels(
    session: AsyncSession,
    sample_id: UUID,
    display_box: dict[str, Any],
    digit_boxes: list[dict[str, Any]],
) -> OcrSample:
    sample = await session.get(OcrSample, sample_id)
    if sample is None:
        raise SampleNotFoundError(f"Sample {sample_id} not found")

    sample.display_box = display_box
    sample.digit_boxes = digit_boxes
    sample.label_source = "manual"
    sample.updated_at = datetime.now(UTC)
    session.add(sample)
    await session.commit()
    await session.refresh(sample)
    return sample


def compute_sample_split(sample: OcrSample, test_sessions: set[str]) -> str:
    stem = Path(sample.photo_path).stem
    if (sample.session and sample.session in test_sessions) or stem in test_sessions:
        return "test"

    sess_str = sample.session or ""
    h = int(hashlib.sha256(sess_str.encode("utf-8")).hexdigest(), 16)
    return "train" if (h % 100) < 80 else "val"


async def create_dataset(
    session: AsyncSession,
    data: DatasetCreate,
) -> tuple[OcrDataset, int, int, int]:
    name_stmt = select(OcrDataset).where(OcrDataset.name == data.name)
    existing = (await session.exec(name_stmt)).first()
    if existing is not None:
        raise DatasetConflictError(f"Dataset '{data.name}' already exists")

    dataset = OcrDataset(name=data.name, test_sessions=data.test_sessions)
    session.add(dataset)
    await session.flush()
    assert dataset.id is not None

    # Resolve sample filter criteria
    f_source = data.source
    f_session = data.session
    f_labeled = data.labeled
    if data.sample_filter:
        if f_source is None:
            f_source = data.sample_filter.get("source")
        if f_session is None:
            f_session = data.sample_filter.get("session")
        if f_labeled is None:
            f_labeled = data.sample_filter.get("labeled")

    conditions = []
    if f_source is not None:
        conditions.append(OcrSample.source == f_source)
    if f_session is not None:
        conditions.append(OcrSample.session == f_session)
    if f_labeled is True:
        conditions.append(
            and_(
                OcrSample.display_box.is_not(None),
                OcrSample.display_box != text("'null'::jsonb"),
            )
        )
    elif f_labeled is False:
        conditions.append(
            or_(
                OcrSample.display_box.is_(None),
                OcrSample.display_box == text("'null'::jsonb"),
            )
        )

    stmt = select(OcrSample)
    if conditions:
        stmt = stmt.where(*conditions)
    samples = (await session.exec(stmt)).all()

    test_set = set(data.test_sessions)
    train_count = 0
    val_count = 0
    test_count = 0

    for sample in samples:
        split = compute_sample_split(sample, test_set)
        if split == "test":
            test_count += 1
        elif split == "val":
            val_count += 1
        else:
            train_count += 1

        assert sample.id is not None
        ds_sample = OcrDatasetSample(
            dataset_id=dataset.id,
            sample_id=sample.id,
            split=split,
        )
        session.add(ds_sample)

    await session.commit()
    await session.refresh(dataset)
    return dataset, train_count, val_count, test_count


async def list_datasets(session: AsyncSession) -> Sequence[OcrDataset]:
    stmt = select(OcrDataset).order_by(OcrDataset.created_at.desc())
    return (await session.exec(stmt)).all()


async def get_dataset(session: AsyncSession, dataset_id: UUID) -> OcrDataset | None:
    return await session.get(OcrDataset, dataset_id)


async def get_dataset_detail(
    session: AsyncSession, dataset_id: UUID
) -> tuple[OcrDataset, int, int, int, int] | None:
    dataset = await session.get(OcrDataset, dataset_id)
    if dataset is None:
        return None

    counts_stmt = (
        select(OcrDatasetSample.split, func.count())
        .where(OcrDatasetSample.dataset_id == dataset_id)
        .group_by(OcrDatasetSample.split)
    )
    counts = dict((await session.exec(counts_stmt)).all())

    train_c = counts.get("train", 0)
    val_c = counts.get("val", 0)
    test_c = counts.get("test", 0)
    total_c = train_c + val_c + test_c
    return dataset, total_c, train_c, val_c, test_c


async def create_job(
    session: AsyncSession,
    dataset_id: UUID,
    params: dict[str, Any],
) -> OcrTrainingJob:
    dataset = await session.get(OcrDataset, dataset_id)
    if dataset is None:
        raise DatasetNotFoundError(f"Dataset {dataset_id} not found")

    job = OcrTrainingJob(dataset_id=dataset_id, status="queued", params=params)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def list_jobs(session: AsyncSession) -> Sequence[OcrTrainingJob]:
    stmt = select(OcrTrainingJob).order_by(OcrTrainingJob.created_at.desc())
    return (await session.exec(stmt)).all()


async def get_job(session: AsyncSession, job_id: UUID) -> OcrTrainingJob | None:
    return await session.get(OcrTrainingJob, job_id)


async def claim_oldest_job(
    session: AsyncSession,
) -> tuple[OcrTrainingJob, list[dict[str, Any]]] | None:
    stmt = (
        select(OcrTrainingJob)
        .where(OcrTrainingJob.status == "queued")
        .order_by(OcrTrainingJob.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    job = (await session.exec(stmt)).first()
    if job is None:
        return None

    job.status = "running"
    job.claimed_at = datetime.now(UTC)
    session.add(job)
    await session.commit()
    await session.refresh(job)

    samples_stmt = (
        select(OcrDatasetSample, OcrSample)
        .join(OcrSample, OcrDatasetSample.sample_id == OcrSample.id)
        .where(OcrDatasetSample.dataset_id == job.dataset_id)
    )
    results = (await session.exec(samples_stmt)).all()

    manifest = []
    for ds_sample, sample in results:
        manifest.append(
            {
                "sample_id": sample.id,
                "split": ds_sample.split,
                "ref_sys": sample.ref_sys,
                "ref_dia": sample.ref_dia,
                "ref_pul": sample.ref_pul,
                "display_box": sample.display_box,
                "digit_boxes": sample.digit_boxes,
                "photo_path": sample.photo_path,
                "photo_url": f"/ocr-trainer/samples/{sample.id}/photo",
            }
        )

    return job, manifest


async def complete_job_with_result(
    session: AsyncSession,
    job_id: UUID,
    report_dict: dict[str, Any],
    display_model_bytes: bytes,
    digit_model_bytes: bytes,
    ocr_models_dir: Path,
) -> OcrModel:
    job = await session.get(OcrTrainingJob, job_id)
    if job is None:
        raise JobNotFoundError(f"Job {job_id} not found")
    if job.status != "running":
        raise JobStateConflictError(f"Job is in '{job.status}' state, expected 'running'")

    version = report_dict.get("version")
    if not version or not isinstance(version, str):
        version = f"job-{job.id}"

    rel_display, rel_digit = storage.save_model_artifacts(
        ocr_models_dir, version, display_model_bytes, digit_model_bytes
    )

    model = OcrModel(
        version=version,
        job_id=job.id,
        display_model_path=rel_display,
        digit_model_path=rel_digit,
        report=report_dict,
        is_current=False,
    )
    session.add(model)
    await session.flush()
    assert model.id is not None

    job.status = "succeeded"
    job.finished_at = datetime.now(UTC)
    job.model_id = model.id
    session.add(job)

    await session.commit()
    await session.refresh(model)
    return model


async def fail_job(
    session: AsyncSession,
    job_id: UUID,
    error: str,
) -> OcrTrainingJob:
    job = await session.get(OcrTrainingJob, job_id)
    if job is None:
        raise JobNotFoundError(f"Job {job_id} not found")
    if job.status != "running":
        raise JobStateConflictError(f"Job is in '{job.status}' state, expected 'running'")

    job.status = "failed"
    job.error = error
    job.finished_at = datetime.now(UTC)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def list_models(session: AsyncSession) -> Sequence[OcrModel]:
    stmt = select(OcrModel).order_by(OcrModel.created_at.desc())
    return (await session.exec(stmt)).all()


async def get_model(session: AsyncSession, model_id: UUID) -> OcrModel | None:
    return await session.get(OcrModel, model_id)


async def make_model_current(session: AsyncSession, model_id: UUID) -> OcrModel:
    model = await session.get(OcrModel, model_id)
    if model is None:
        raise ModelNotFoundError(f"Model {model_id} not found")

    await session.exec(
        update(OcrModel).where(OcrModel.is_current.is_(True)).values(is_current=False)
    )
    model.is_current = True
    session.add(model)
    await session.commit()
    await session.refresh(model)
    return model
