from fastapi import APIRouter

from reminders.router.deps import get_current_user_id  # re-exported for conftest.py override
from reminders.router.intake_report import router as intake_report_router
from reminders.router.reminder_config import router as reminder_config_router

__all__ = ["get_current_user_id", "router"]

router = APIRouter(tags=["reminders"])
router.include_router(reminder_config_router)
router.include_router(intake_report_router)
