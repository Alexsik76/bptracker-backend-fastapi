from fastapi import APIRouter

from prescriptions.router.deps import get_current_user_id  # re-exported for conftest.py override
from prescriptions.router.medication_item import router as medication_item_router
from prescriptions.router.prescription import router as prescription_router

__all__ = ["get_current_user_id", "router"]

router = APIRouter(tags=["prescriptions"])
router.include_router(prescription_router)
router.include_router(medication_item_router)
