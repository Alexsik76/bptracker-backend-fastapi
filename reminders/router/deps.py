# Re-export the real auth dependency so sub-routers keep importing CurrentUserId
# from here. The temporary hardcoded-UUID stub has been removed now that the auth
# module provides real token-based identification.
from auth.deps import CurrentUserId, get_current_user_id

__all__ = ["CurrentUserId", "get_current_user_id"]
