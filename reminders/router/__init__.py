from fastapi import APIRouter

from reminders.router.intake_report import router as intake_report_router
from reminders.router.reminder_config import router as reminder_config_router

__all__ = ["router"]

router = APIRouter(tags=["reminders"])
router.include_router(reminder_config_router)
router.include_router(intake_report_router)
