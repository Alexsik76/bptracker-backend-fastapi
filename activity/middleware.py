import time
import traceback
from datetime import UTC, datetime
from uuid import UUID

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from activity.models import ErrorEvent, RequestEvent
from activity.writer import record_activity_event
from auth.security import decode_access_token
from config import get_settings


class ActivityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "")

        if path == "/health" or method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        settings = get_settings()
        if not settings.activity_log_enabled:
            await self.app(scope, receive, send)
            return

        start_time = time.perf_counter()
        occurred_at = datetime.now(UTC)
        headers = Headers(scope=scope)

        # 1. client: X-Client header else android if okhttp in UA else other
        client_header = headers.get("x-client")
        if client_header:
            client = client_header[:32]
        elif "okhttp" in headers.get("user-agent", "").lower():
            client = "android"
        else:
            client = "other"

        # 2. module: first path segment (e.g. measurements, nutrition, auth)
        segments = [s for s in path.strip("/").split("/") if s]
        module = segments[0] if segments else ""

        # 3. user_id: decoded bearer token without DB lookup
        user_id: UUID | None = None
        auth_header = headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            try:
                user_id = decode_access_token(token)
            except Exception:
                user_id = None

        status_code = 500
        exc_occurred: Exception | None = None

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            exc_occurred = exc
            raise
        finally:
            duration_ms = max(0, int(round((time.perf_counter() - start_time) * 1000)))
            route_obj = scope.get("route")
            route_template = getattr(route_obj, "path", None)
            if route_template is None:
                # Unmatched paths (scanners, typos) must not create unbounded keys.
                route_template = "(unmatched)"
                module = "(unmatched)"

            if exc_occurred is not None:
                tb = "".join(traceback.format_exception(exc_occurred))
                if len(tb) > 20000:
                    tb = tb[:20000]
                error_event = ErrorEvent(
                    occurred_at=occurred_at,
                    user_id=user_id,
                    method=method,
                    route=route_template,
                    exception_type=type(exc_occurred).__name__,
                    message=str(exc_occurred),
                    traceback=tb,
                )
                record_activity_event(error_event)

            request_event = RequestEvent(
                occurred_at=occurred_at,
                user_id=user_id,
                client=client,
                module=module,
                method=method,
                route=route_template,
                status_code=status_code,
                duration_ms=duration_ms,
            )
            record_activity_event(request_event)
