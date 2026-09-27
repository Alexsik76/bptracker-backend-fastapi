from fastapi import APIRouter

from prescriptions.router.medication_item import router as medication_item_router
from prescriptions.router.prescription import router as prescription_router

__all__ = ["router"]

router = APIRouter(tags=["prescriptions"])
router.include_router(prescription_router)
router.include_router(medication_item_router)
