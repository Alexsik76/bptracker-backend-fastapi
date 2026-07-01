from collections.abc import Sequence
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from db import SessionDep
from reminders import crud
from reminders.models import IntakeReport, IntakeReportCreate, IntakeReportRead
from reminders.router.deps import CurrentUserId

router = APIRouter(prefix="/reminders/intake-reports")


@router.post("", response_model=IntakeReportRead, status_code=status.HTTP_201_CREATED)
async def create_intake_report(
    data: IntakeReportCreate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> IntakeReport:
    try:
        report = await crud.create_intake_report(session, data, user_id)
    except crud.IntakeReportAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Intake already confirmed for this slot and date",
        ) from exc
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reminder config not found; set it up before confirming intakes",
        )
    return report


@router.get("", response_model=Sequence[IntakeReportRead])
async def list_intake_reports(
    session: SessionDep,
    user_id: CurrentUserId,
) -> Sequence[IntakeReport]:
    return await crud.get_intake_reports(session, user_id)


@router.get("/{report_id}", response_model=IntakeReportRead)
async def get_intake_report(
    report_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> IntakeReport:
    report = await crud.get_intake_report(session, report_id, user_id)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Intake report not found"
        )
    return report
