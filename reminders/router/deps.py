from typing import Annotated
from uuid import UUID

from fastapi import Depends

# --- Temporary auth stand-in -------------------------------------------------
# Same seam as measurements/router.py and prescriptions/router/deps.py: a single
# hardcoded dev user until the auth module lands. Each module keeps its own copy
# of this stub (no shared constant) — unifying them is a deliberate future TODO.
_DEV_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


async def get_current_user_id() -> UUID:
    return _DEV_USER_ID


CurrentUserId = Annotated[UUID, Depends(get_current_user_id)]
# -----------------------------------------------------------------------------
