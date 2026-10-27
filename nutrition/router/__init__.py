from fastapi import APIRouter

from nutrition.router.dish import router as dish_router
from nutrition.router.menu import items_router, menus_router
from nutrition.router.off_plan import router as off_plan_router
from nutrition.router.pinned_meal import router as pinned_meal_router
from nutrition.router.product import router as product_router
from nutrition.router.profile import router as profile_router

router = APIRouter(prefix="/nutrition", tags=["nutrition"])
router.include_router(profile_router)
router.include_router(product_router)
router.include_router(dish_router)
router.include_router(pinned_meal_router)
router.include_router(menus_router)
router.include_router(items_router)
router.include_router(off_plan_router)

__all__ = ["router"]

