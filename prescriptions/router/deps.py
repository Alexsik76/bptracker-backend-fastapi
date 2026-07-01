from typing import Annotated
from uuid import UUID

from fastapi import Depends

# --- Temporary auth stand-in -------------------------------------------------
# Same seam as measurements/router.py: single hardcoded dev user until the
# auth module lands. Lives here (not in __init__.py) so prescription.py and
# medication_item.py can both import it without a circular import.
_DEV_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


async def get_current_user_id() -> UUID:
    return _DEV_USER_ID


CurrentUserId = Annotated[UUID, Depends(get_current_user_id)]
# -----------------------------------------------------------------------------
