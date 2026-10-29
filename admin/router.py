from fastapi import APIRouter

from auth.deps import CurrentAdminId

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/ping")
async def ping(_: CurrentAdminId) -> dict[str, str]:
    return {"status": "ok"}
